"""Subsonic + native Navidrome HTTP client.

Discovery stays on YouTube Music. This client is only for the user's own
library: search/match, stream, playlists and a full-library scan after import.
"""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.core.logging import logger
from app.navidrome import config, ids as nd_ids

CLIENT_NAME = "MusicPlay"
API_VERSION = "1.16.1"


class NavidromeError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _cover_url(cover_id: str | None) -> str | None:
    if not cover_id:
        return None
    return f"/api/navidrome/cover/{cover_id}"


def map_song(raw: dict, *, youtube_id: str | None = None) -> dict:
    navidrome_id = str(raw.get("id") or "")
    duration = raw.get("duration") or 0
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 0
    track_id = youtube_id or nd_ids.wrap(navidrome_id)
    return {
        "id": track_id,
        "title": raw.get("title") or raw.get("name") or "",
        "artist": raw.get("artist") or raw.get("albumArtist") or "",
        "artist_id": nd_ids.wrap(str(raw.get("artistId") or "")) or None,
        "album": raw.get("album") or "",
        "album_id": nd_ids.wrap(str(raw.get("albumId") or "")) or None,
        "duration": duration,
        "thumbnail": _cover_url(raw.get("coverArt") or raw.get("albumId") or navidrome_id),
        "year": raw.get("year"),
        "track_no": raw.get("track") or raw.get("trackNumber"),
        "disc_no": raw.get("discNumber"),
        "genre": raw.get("genre"),
        "path": raw.get("path") or "",
        "suffix": raw.get("suffix") or "",
        "bitrate": raw.get("bitRate") or raw.get("bitrate") or 0,
        "navidromeId": navidrome_id,
        "inLibrary": True,
        "source": "navidrome" if not youtube_id else "both",
        "kind": "song",
    }


def map_album(raw: dict) -> dict:
    album_id = str(raw.get("id") or "")
    return {
        "id": nd_ids.wrap(album_id),
        "name": raw.get("name") or raw.get("title") or "",
        "title": raw.get("name") or raw.get("title") or "",
        "artist": raw.get("artist") or raw.get("albumArtist") or "",
        "artist_id": nd_ids.wrap(str(raw.get("artistId") or "")) or None,
        "thumbnail": _cover_url(raw.get("coverArt") or album_id),
        "year": raw.get("year"),
        "song_count": raw.get("songCount") or 0,
        "duration": raw.get("duration") or 0,
        "navidromeId": album_id,
        "source": "navidrome",
        "kind": "album",
        "inLibrary": True,
    }


def map_artist(raw: dict) -> dict:
    artist_id = str(raw.get("id") or "")
    return {
        "id": nd_ids.wrap(artist_id),
        "name": raw.get("name") or "",
        "title": raw.get("name") or "",
        "thumbnail": _cover_url(raw.get("coverArt") or artist_id),
        "album_count": raw.get("albumCount") or 0,
        "navidromeId": artist_id,
        "source": "navidrome",
        "kind": "artist",
        "inLibrary": True,
    }


def map_playlist(raw: dict) -> dict:
    playlist_id = str(raw.get("id") or "")
    return {
        "id": nd_ids.wrap(playlist_id),
        "name": raw.get("name") or "",
        "comment": raw.get("comment") or "",
        "song_count": raw.get("songCount") or 0,
        "duration": raw.get("duration") or 0,
        "thumbnail": _cover_url(raw.get("coverArt") or playlist_id),
        "navidromeId": playlist_id,
        "source": "navidrome",
        "kind": "playlist",
        "owner": raw.get("owner") or "",
    }


class NavidromeClient:
    def __init__(self, data: dict | None = None):
        self.data = data or config.load()
        self.base = (self.data.get("url") or "").rstrip("/")
        self.username = self.data.get("username") or ""
        self.password = self.data.get("password") or ""
        self._jwt: str | None = None
        self._http = httpx.Client(follow_redirects=True, timeout=httpx.Timeout(12.0, read=60.0))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "NavidromeClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def ready(self) -> bool:
        return bool(self.base and self.username and self.password)

    def _auth_params(self) -> dict[str, str]:
        salt = secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": API_VERSION,
            "c": CLIENT_NAME,
            "f": "json",
        }

    def rest_url(self, method: str, extra: dict | None = None) -> str:
        params = self._auth_params()
        if extra:
            for key, value in extra.items():
                if value is None or value == "":
                    continue
                params[key] = value
        endpoint = method if method.endswith(".view") else f"{method}.view"
        return f"{self.base}/rest/{endpoint}?{urlencode(params, doseq=True)}"

    def _rest(self, method: str, extra: dict | None = None) -> dict:
        if not self.ready:
            raise NavidromeError("Navidrome ist nicht konfiguriert", 400)
        url = self.rest_url(method, extra)
        try:
            response = self._http.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise NavidromeError(f"Navidrome nicht erreichbar: {exc}") from exc
        except ValueError as exc:
            raise NavidromeError("Navidrome lieferte keine JSON-Antwort") from exc

        body = payload.get("subsonic-response") or payload
        if body.get("status") != "ok":
            error = body.get("error") or {}
            raise NavidromeError(error.get("message") or "Navidrome-Fehler", 502)
        return body

    def ping(self) -> dict:
        body = self._rest("ping")
        return {
            "ok": True,
            "type": body.get("type") or "navidrome",
            "version": body.get("serverVersion") or body.get("version") or "",
        }

    def search(self, query: str, songs: int = 25, albums: int = 8, artists: int = 8) -> dict:
        body = self._rest(
            "search3",
            {
                "query": query,
                "songCount": songs,
                "albumCount": albums,
                "artistCount": artists,
            },
        )
        result = body.get("searchResult3") or {}
        return {
            "songs": [map_song(item) for item in _as_list(result.get("song"))],
            "albums": [map_album(item) for item in _as_list(result.get("album"))],
            "artists": [map_artist(item) for item in _as_list(result.get("artist"))],
        }

    def get_song(self, song_id: str) -> dict | None:
        body = self._rest("getSong", {"id": song_id})
        raw = body.get("song")
        return map_song(raw) if raw else None

    def get_album(self, album_id: str) -> dict | None:
        body = self._rest("getAlbum", {"id": album_id})
        raw = body.get("album")
        if not raw:
            return None
        album = map_album(raw)
        album["tracks"] = [map_song(item) for item in _as_list(raw.get("song"))]
        album["song_count"] = len(album["tracks"])
        return album

    def get_artist(self, artist_id: str) -> dict | None:
        body = self._rest("getArtist", {"id": artist_id})
        raw = body.get("artist")
        if not raw:
            return None
        artist = map_artist(raw)
        artist["albums"] = [map_album(item) for item in _as_list(raw.get("album"))]
        songs: list[dict] = []
        for album in artist["albums"][:8]:
            detail = self.get_album(nd_ids.unwrap(album["id"]) or "")
            if detail:
                songs.extend(detail.get("tracks") or [])
        artist["top_songs"] = songs[:20]
        artist["related"] = []
        return artist

    def album_list(self, list_type: str = "recent", size: int = 24, offset: int = 0) -> list[dict]:
        body = self._rest("getAlbumList2", {"type": list_type, "size": size, "offset": offset})
        listing = body.get("albumList2") or {}
        return [map_album(item) for item in _as_list(listing.get("album"))]

    def playlists(self) -> list[dict]:
        body = self._rest("getPlaylists")
        listing = body.get("playlists") or {}
        return [map_playlist(item) for item in _as_list(listing.get("playlist"))]

    def get_playlist(self, playlist_id: str) -> dict | None:
        body = self._rest("getPlaylist", {"id": playlist_id})
        raw = body.get("playlist")
        if not raw:
            return None
        playlist = map_playlist(raw)
        playlist["tracks"] = [map_song(item) for item in _as_list(raw.get("entry"))]
        playlist["song_count"] = len(playlist["tracks"])
        return playlist

    def create_playlist(self, name: str, song_ids: list[str], playlist_id: str | None = None) -> str | None:
        params = self._auth_params()
        if playlist_id:
            params["id"] = playlist_id
        params["name"] = name
        pairs = list(params.items())
        for song_id in song_ids:
            pairs.append(("songId", song_id))
        try:
            response = self._http.post(f"{self.base}/rest/createPlaylist.view", data=pairs)
            response.raise_for_status()
            body = (response.json() or {}).get("subsonic-response") or {}
        except httpx.HTTPError as exc:
            raise NavidromeError(f"Playlist konnte nicht gespeichert werden: {exc}") from exc
        if body.get("status") != "ok":
            error = body.get("error") or {}
            raise NavidromeError(error.get("message") or "Playlist fehlgeschlagen")
        created = body.get("playlist") or {}
        return str(created.get("id") or playlist_id or "")

    def delete_playlist(self, playlist_id: str) -> None:
        self._rest("deletePlaylist", {"id": playlist_id})

    def start_scan(self) -> None:
        try:
            self._rest("startScan")
        except NavidromeError as exc:
            logger.warning("Navidrome-Scan konnte nicht gestartet werden: {}", exc)

    def scan_status(self) -> dict:
        try:
            body = self._rest("getScanStatus")
        except NavidromeError:
            return {"scanning": False, "count": 0}
        status = body.get("scanStatus") or {}
        return {"scanning": bool(status.get("scanning")), "count": status.get("count") or 0}

    def stream_url(self, song_id: str) -> str:
        return self.rest_url("stream", {"id": song_id, "format": "raw"})

    def cover_url(self, cover_id: str, size: int = 544) -> str:
        return self.rest_url("getCoverArt", {"id": cover_id, "size": size})

    def login_native(self) -> str | None:
        if self._jwt:
            return self._jwt
        try:
            response = self._http.post(
                f"{self.base}/auth/login",
                json={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        token = payload.get("token")
        if token:
            self._jwt = token
        return self._jwt

    def list_songs(self, limit: int = 20000, page: int = 500) -> list[dict]:
        """Pull the whole library via the native API when available."""
        token = self.login_native()
        if not token:
            return []

        songs: list[dict] = []
        start = 0
        headers = {"x-nd-authorization": f"Bearer {token}"}
        while start < limit:
            end = min(start + page, limit)
            try:
                response = self._http.get(
                    f"{self.base}/api/song",
                    params={"_start": start, "_end": end, "_sort": "title", "_order": "ASC"},
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                batch = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("Navidrome /api/song nicht verfügbar: {}", exc)
                break
            if not isinstance(batch, list) or not batch:
                break
            for raw in batch:
                songs.append(
                    {
                        "id": str(raw.get("id") or ""),
                        "title": raw.get("title") or "",
                        "artist": raw.get("artist") or "",
                        "album": raw.get("album") or "",
                        "duration": int(raw.get("duration") or 0),
                        "path": raw.get("path") or "",
                        "albumId": raw.get("albumId"),
                        "artistId": raw.get("artistId"),
                        "coverArt": raw.get("coverArtId") or raw.get("albumId") or raw.get("id"),
                        "year": raw.get("year"),
                        "track": raw.get("trackNumber"),
                        "suffix": raw.get("suffix") or "",
                        "bitRate": raw.get("bitRate") or 0,
                    }
                )
            if len(batch) < page:
                break
            start = end
        return songs
