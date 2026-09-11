"""HTTP client for MediaSync-Hub ingest (download a track into a playlist)."""

from __future__ import annotations

import httpx

from app.mediasync import config


class MediaSyncError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class MediaSyncClient:
    def __init__(self, data: dict | None = None):
        self.data = data or config.load()
        self.base = (self.data.get("url") or "").rstrip("/")
        self.username = self.data.get("username") or ""
        self.password = self.data.get("password") or ""
        auth = (self.username, self.password) if self.username and self.password else None
        self._http = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(12.0, read=30.0),
            auth=auth,
            headers={"Accept": "application/json", "User-Agent": "MusicPlay"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MediaSyncClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def ready(self) -> bool:
        return bool(self.base)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base}{path}"

    def _get(self, path: str) -> httpx.Response:
        response = self._http.get(self._url(path))
        if response.status_code >= 400:
            raise MediaSyncError(self._error(response), response.status_code)
        return response

    def _post(self, path: str, payload: dict) -> httpx.Response:
        response = self._http.post(self._url(path), json=payload)
        if response.status_code >= 400:
            raise MediaSyncError(self._error(response), response.status_code)
        return response

    def _error(self, response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = body.get("detail") or body.get("error") or body.get("message")
            if isinstance(detail, str) and detail:
                return detail
        except ValueError:
            pass
        return f"MediaSync HTTP {response.status_code}"

    def ping(self) -> dict:
        try:
            response = self._http.get(self._url("/health"), timeout=8.0)
            response.raise_for_status()
            payload = response.json() if response.content else {}
        except httpx.HTTPError as exc:
            raise MediaSyncError(f"MediaSync nicht erreichbar: {exc}") from exc
        version = payload.get("version") or ""
        if not version or version == "ok":
            try:
                version_payload = self._http.get(self._url("/version"), timeout=8.0).json()
                version = version_payload.get("version") or version
            except Exception:
                pass
        return {"ok": True, "version": version or payload.get("status") or "ok"}

    def targets(self) -> list[dict]:
        try:
            payload = self._get("/api/ingest/targets").json()
            items = payload.get("targets") or []
            if items:
                return items
        except MediaSyncError:
            pass

        # Older MediaSync: Jellyfin playlist list only.
        try:
            raw = self._get("/playlist/list").json()
        except MediaSyncError as exc:
            raise MediaSyncError(str(exc)) from exc
        playlists = raw if isinstance(raw, list) else raw.get("playlists") or []
        destinations = [{"id": "library", "name": "Nur Bibliothek", "kind": "library"}]
        for playlist in playlists:
            destinations.append(
                {
                    "id": playlist.get("id") or playlist.get("Id") or "",
                    "name": playlist.get("name") or playlist.get("Name") or "Playlist",
                    "kind": "playlist",
                }
            )
        return [item for item in destinations if item.get("id")]

    def send(self, track: dict, playlist_id: str | None = None, playlist_name: str | None = None) -> dict:
        payload = {
            "artist": track.get("artist") or "",
            "title": track.get("title") or "",
            "album": track.get("album") or "",
            "playlistId": playlist_id or "",
            "playlistName": playlist_name or "",
            "source": "music-play",
        }
        try:
            body = self._post("/api/ingest", payload).json()
        except MediaSyncError as exc:
            if exc.status_code == 404:
                return self._send_tracks_api(track, playlist_id, playlist_name)
            raise
        return body

    def _send_tracks_api(
        self,
        track: dict,
        playlist_id: str | None,
        playlist_name: str | None,
    ) -> dict:
        """MediaSync 0.3.x native import when /api/ingest is not deployed yet."""
        target_id = (playlist_id or "").strip()
        if target_id in ("library", "none"):
            target_id = ""
        name = (playlist_name or "").strip()
        native = {
            "artist": track.get("artist") or "",
            "title": track.get("title") or "",
            "album": track.get("album") or "",
            "playlist_id": target_id or None,
            "new_playlist_name": name or None,
            "target": "library" if not target_id and not name else "playlist",
            "async": True,
        }
        try:
            body = self._post("/api/tracks", native).json()
        except MediaSyncError as exc:
            if exc.status_code == 404:
                raise MediaSyncError(
                    "MediaSync kann keine Titel entgegennehmen. Container auf 0.3.28 aktualisieren.",
                    404,
                ) from exc
            raise
        job_id = body.get("job_id") or ""
        return {
            "ok": True,
            "job": {
                "id": job_id,
                "status": "queued" if job_id else body.get("status") or "done",
                "artist": body.get("artist") or track.get("artist") or "",
                "title": body.get("title") or track.get("title") or "",
                "playlistId": target_id,
                "playlistName": name,
                "error": None,
                "result": None,
            },
        }
