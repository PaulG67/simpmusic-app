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
        video_id = track.get("id") or ""
        payload = {
            "artist": track.get("artist") or "",
            "title": track.get("title") or "",
            "album": track.get("album") or "",
            "youtubeId": video_id if not str(video_id).startswith("nd:") else "",
            "videoId": video_id if not str(video_id).startswith("nd:") else "",
            "youtubeUrl": f"https://music.youtube.com/watch?v={video_id}" if video_id and not str(video_id).startswith("nd:") else "",
            "playlistId": playlist_id or "",
            "playlistName": playlist_name or "",
            "source": "music-play",
        }
        try:
            body = self._post("/api/ingest", payload).json()
        except MediaSyncError as exc:
            if exc.status_code == 404:
                raise MediaSyncError(
                    "MediaSync hat keine Ingest-API. Den MediaSync-Container auf 0.2.0 aktualisieren.",
                    404,
                ) from exc
            raise
        return body
