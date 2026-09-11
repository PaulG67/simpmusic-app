"""Thin, cached wrapper around ytmusicapi.

ytmusicapi is synchronous and its client object is not safe to share across
threads, so each worker thread gets its own instance. All public methods return
normalised dicts (see `mapper`), never raw ytmusicapi payloads.
"""

import threading
from pathlib import Path
from typing import Any

from ytmusicapi import YTMusic

from app.core.logging import logger
from app.core.settings import settings
from app.ytm import mapper
from app.ytm.cache import TTLCache

SEARCH_FILTERS = {
    "songs": "songs",
    "videos": "videos",
    "albums": "albums",
    "artists": "artists",
    "playlists": "playlists",
    "community_playlists": "community_playlists",
    "featured_playlists": "featured_playlists",
}

_cache = TTLCache(ttl=settings.metadata_ttl)
_local = threading.local()


def _build_client() -> YTMusic:
    auth = None
    if settings.ytm_auth_file:
        auth_path = Path(settings.ytm_auth_file)
        if auth_path.exists():
            auth = str(auth_path)
            logger.info("YouTube Music: verwende Anmeldung aus {}", auth_path)
        else:
            logger.warning("YTM_AUTH_FILE gesetzt, aber {} existiert nicht", auth_path)

    try:
        return YTMusic(auth, language=settings.ytm_language, location=settings.ytm_location)
    except Exception as exc:
        logger.warning("YouTube Music mit Sprache/Region fehlgeschlagen ({}), nutze Standard", exc)
        return YTMusic(auth)


def client() -> YTMusic:
    instance = getattr(_local, "client", None)
    if instance is None:
        instance = _build_client()
        _local.client = instance
    return instance


def is_authenticated() -> bool:
    return bool(settings.ytm_auth_file and Path(settings.ytm_auth_file).exists())


def _cached(key: str, producer, ttl: int | None = None) -> Any:
    hit = _cache.get(key)
    if hit is not None:
        return hit
    value = producer()
    if value is not None:
        _cache.set(key, value, ttl)
    return value


def clear_cache() -> None:
    _cache.clear()


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def search(query: str, scope: str = "songs", limit: int = 25) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []

    ytm_filter = SEARCH_FILTERS.get(scope)
    key = f"search:{scope}:{limit}:{query.lower()}"

    def produce() -> list[dict]:
        try:
            results = client().search(query, filter=ytm_filter, limit=limit)
        except Exception as exc:
            logger.error("Suche fehlgeschlagen ({}): {}", scope, exc)
            return []
        return _normalize_results(results, scope)

    return _cached(key, produce, ttl=900) or []


def _normalize_results(results: list[dict], scope: str) -> list[dict]:
    normalized: list[dict] = []
    for raw in results or []:
        result_type = raw.get("resultType") or scope.rstrip("s")
        if result_type in ("song", "video", "episode"):
            item = mapper.normalize_episode(raw) if result_type == "episode" else mapper.normalize_song(raw)
            kind = "song"
        elif result_type == "album":
            item = mapper.normalize_album(raw)
            kind = "album"
        elif result_type == "artist":
            item = mapper.normalize_artist(raw)
            kind = "artist"
        elif result_type == "playlist":
            item = mapper.normalize_playlist(raw)
            kind = "playlist"
        elif result_type == "podcast":
            item = mapper.normalize_podcast(raw)
            kind = "podcast"
        else:
            continue
        if item:
            item["kind"] = kind
            normalized.append(item)
    return normalized


def search_all(query: str, song_limit: int = 25, album_limit: int = 10, artist_limit: int = 10) -> dict:
    return {
        "songs": search(query, "songs", song_limit),
        "albums": search(query, "albums", album_limit),
        "artists": search(query, "artists", artist_limit),
    }


def suggestions(query: str) -> list[str]:
    query = (query or "").strip()
    if not query:
        return []
    try:
        return _cached(f"sug:{query.lower()}", lambda: client().get_search_suggestions(query), ttl=900) or []
    except Exception as exc:
        logger.debug("Suchvorschläge fehlgeschlagen: {}", exc)
        return []


# --------------------------------------------------------------------------
# Browsing
# --------------------------------------------------------------------------


def album(browse_id: str) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_album(browse_id)
        except Exception as exc:
            logger.error("Album {} konnte nicht geladen werden: {}", browse_id, exc)
            return None

        info = mapper.normalize_album({**raw, "browseId": browse_id})
        if info is None:
            return None

        hint = {
            "name": info["name"],
            "id": browse_id,
            "thumbnail": info["thumbnail"],
            "artist": info["artist"],
            "artist_id": info["artist_id"],
            "year": info["year"],
        }
        tracks = []
        for position, raw_track in enumerate(raw.get("tracks") or [], start=1):
            track = mapper.normalize_song(raw_track, album_hint=hint)
            if track is None:
                continue
            track["track_no"] = track.get("track_no") or position
            tracks.append(track)

        info["tracks"] = tracks
        info["song_count"] = info.get("song_count") or len(tracks)
        info["duration"] = info.get("duration") or sum(int(t.get("duration") or 0) for t in tracks)
        return info

    return _cached(f"album:{browse_id}", produce)


def artist(channel_id: str) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_artist(channel_id)
        except Exception as exc:
            logger.error("Interpret {} konnte nicht geladen werden: {}", channel_id, exc)
            return None

        info = mapper.normalize_artist({**raw, "channelId": channel_id})
        if info is None:
            return None

        albums: list[dict] = []
        for section in ("albums", "singles"):
            block = raw.get(section) or {}
            for raw_album in block.get("results") or []:
                item = mapper.normalize_album(raw_album)
                if item:
                    item.setdefault("artist", info["name"])
                    item.setdefault("artist_id", channel_id)
                    albums.append(item)

        top_songs = []
        songs_block = raw.get("songs") or {}
        for raw_song in songs_block.get("results") or []:
            track = mapper.normalize_song(raw_song)
            if track:
                top_songs.append(track)

        related = []
        for raw_related in (raw.get("related") or {}).get("results") or []:
            item = mapper.normalize_artist(raw_related)
            if item:
                related.append(item)

        info["albums"] = albums
        info["album_count"] = len(albums)
        info["top_songs"] = top_songs
        info["related"] = related
        info["songs_playlist_id"] = songs_block.get("browseId")
        return info

    return _cached(f"artist:{channel_id}", produce)


def song(video_id: str) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_song(video_id)
        except Exception as exc:
            logger.error("Titel {} konnte nicht geladen werden: {}", video_id, exc)
            return None

        details = (raw or {}).get("videoDetails") or {}
        if not details:
            return None

        return mapper.normalize_song(
            {
                "videoId": video_id,
                "title": details.get("title"),
                "artists": [{"name": details.get("author"), "id": details.get("channelId")}],
                "lengthSeconds": details.get("lengthSeconds"),
                "thumbnails": (details.get("thumbnail") or {}).get("thumbnails"),
            }
        )

    return _cached(f"song:{video_id}", produce)


def playlist(playlist_id: str, limit: int = 200) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_playlist(playlist_id, limit=limit)
        except Exception as exc:
            logger.error("Playlist {} konnte nicht geladen werden: {}", playlist_id, exc)
            return None

        info = mapper.normalize_playlist({**raw, "playlistId": playlist_id})
        if info is None:
            return None

        tracks = []
        for raw_track in raw.get("tracks") or []:
            if raw_track.get("isAvailable") is False:
                continue
            track = mapper.normalize_song(raw_track)
            if track:
                tracks.append(track)

        info["tracks"] = tracks
        info["song_count"] = info.get("song_count") or len(tracks)
        info["duration"] = info.get("duration") or sum(int(t.get("duration") or 0) for t in tracks)
        return info

    return _cached(f"playlist:{playlist_id}:{limit}", produce, ttl=900)


def radio(video_id: str, limit: int = 30) -> list[dict]:
    """The endless mix YouTube Music builds around a track."""

    def produce() -> list[dict]:
        try:
            raw = client().get_watch_playlist(videoId=video_id, limit=limit, radio=True)
        except Exception as exc:
            logger.error("Radio für {} fehlgeschlagen: {}", video_id, exc)
            return []
        tracks = []
        for raw_track in (raw or {}).get("tracks") or []:
            track = mapper.normalize_song(raw_track)
            if track:
                tracks.append(track)
        return tracks

    return _cached(f"radio:{video_id}:{limit}", produce, ttl=1800) or []


def similar_songs(video_id: str, limit: int = 20) -> list[dict]:
    tracks = radio(video_id, limit=limit + 1)
    return [track for track in tracks if track["id"] != video_id][:limit]


def home(limit: int = 6) -> list[dict]:
    def produce() -> list[dict]:
        try:
            sections = client().get_home(limit=limit)
        except Exception as exc:
            logger.error("Startseite konnte nicht geladen werden: {}", exc)
            return []

        result = []
        for section in sections or []:
            items = []
            for raw in section.get("contents") or []:
                if not isinstance(raw, dict):
                    continue
                browse_id = raw.get("browseId") or ""
                if raw.get("videoId"):
                    item = mapper.normalize_song(raw)
                    kind = "song"
                elif raw.get("playlistId") and not browse_id.startswith("MPRE"):
                    item = mapper.normalize_playlist(raw)
                    kind = "playlist"
                elif browse_id.startswith("MPRE"):
                    item = mapper.normalize_album(raw)
                    kind = "album"
                elif browse_id.startswith("UC"):
                    item = mapper.normalize_artist(raw)
                    kind = "artist"
                else:
                    continue
                if item:
                    item["kind"] = kind
                    items.append(item)
            if items:
                result.append({"title": section.get("title") or "Für dich", "items": items})
        return result

    return _cached(f"home:{limit}:{settings.ytm_location}", produce, ttl=1800) or []


def charts(country: str | None = None) -> list[dict]:
    code = country or settings.ytm_location or "ZZ"

    def produce() -> list[dict]:
        try:
            raw = client().get_charts(country=code)
        except Exception as exc:
            logger.warning("Charts für {} nicht verfügbar: {}", code, exc)
            return []

        sections = []
        for key, title in (("trending", "Trending"), ("videos", "Top Videos"), ("songs", "Top Songs")):
            block = raw.get(key) or {}
            items = block.get("items") if isinstance(block, dict) else block
            tracks = []
            for raw_track in items or []:
                track = mapper.normalize_song(raw_track)
                if track:
                    track["kind"] = "song"
                    tracks.append(track)
            if tracks:
                sections.append({"title": title, "items": tracks})

        artists_block = (raw.get("artists") or {}).get("items") or []
        artists = []
        for raw_artist in artists_block:
            item = mapper.normalize_artist(raw_artist)
            if item:
                item["kind"] = "artist"
                artists.append(item)
        if artists:
            sections.append({"title": "Top Interpreten", "items": artists})

        return sections

    return _cached(f"charts:{code}", produce, ttl=3600) or []


def mood_categories() -> list[dict]:
    """YouTube Music's "Moods & Genres" tree, flattened into sections."""

    def produce() -> list[dict]:
        try:
            raw = client().get_mood_categories()
        except Exception as exc:
            logger.error("Stimmungen/Genres konnten nicht geladen werden: {}", exc)
            return []

        sections = []
        for title, entries in (raw or {}).items():
            items = [
                {"title": entry.get("title"), "params": entry.get("params")}
                for entry in entries or []
                if entry.get("params")
            ]
            if items:
                sections.append({"title": title, "items": items})
        return sections

    return _cached("moods", produce, ttl=86400) or []


def mood_playlists(params: str) -> list[dict]:
    def produce() -> list[dict]:
        try:
            raw = client().get_mood_playlists(params)
        except Exception as exc:
            logger.error("Playlists für Kategorie konnten nicht geladen werden: {}", exc)
            return []
        items = []
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            try:
                browse = str(entry.get("browseId") or entry.get("playlistId") or entry.get("id") or "")
                if browse.startswith("MPRE") or browse.startswith("VLMPRE"):
                    item = mapper.normalize_album(entry)
                    if item:
                        item["kind"] = "album"
                        items.append(item)
                    continue
                item = mapper.normalize_playlist(entry)
                if item:
                    item["kind"] = "playlist"
                    items.append(item)
            except Exception as exc:
                logger.debug("Kategorie-Eintrag übersprungen: {}", exc)
        return items

    return _cached(f"mood:{params}", produce, ttl=21600) or []


def podcast(podcast_id: str, limit: int = 100) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_podcast(podcast_id, limit=limit)
        except Exception as exc:
            logger.error("Podcast {} konnte nicht geladen werden: {}", podcast_id, exc)
            return None

        info = mapper.normalize_podcast({**raw, "podcastId": podcast_id})
        if info is None:
            return None

        episodes = []
        for raw_episode in raw.get("episodes") or []:
            episode = mapper.normalize_episode(raw_episode, podcast_hint=info)
            if episode:
                episodes.append(episode)

        info["episodes"] = episodes
        return info

    return _cached(f"podcast:{podcast_id}:{limit}", produce, ttl=3600)


def episode(video_id: str) -> dict | None:
    def produce() -> dict | None:
        try:
            raw = client().get_episode(video_id)
        except Exception as exc:
            logger.debug("Episode {} konnte nicht geladen werden: {}", video_id, exc)
            return None
        return mapper.normalize_episode({**(raw or {}), "videoId": video_id})

    return _cached(f"episode:{video_id}", produce)


def new_episodes() -> list[dict]:
    """YouTube Music's auto-generated "New Episodes" playlist."""

    def produce() -> list[dict]:
        try:
            raw = client().get_episodes_playlist()
        except Exception as exc:
            logger.debug("Neue Episoden nicht verfügbar: {}", exc)
            return []
        episodes = []
        for raw_episode in (raw or {}).get("episodes") or []:
            item = mapper.normalize_episode(raw_episode)
            if item:
                episodes.append(item)
        return episodes

    return _cached("episodes:new", produce, ttl=3600) or []


def search_podcasts(query: str, limit: int = 20) -> list[dict]:
    def produce() -> list[dict]:
        try:
            raw = client().search(query, filter="podcasts", limit=limit)
        except Exception as exc:
            logger.error("Podcast-Suche fehlgeschlagen: {}", exc)
            return []
        items = []
        for entry in raw or []:
            item = mapper.normalize_podcast(entry)
            if item:
                item["kind"] = "podcast"
                items.append(item)
        return items

    return _cached(f"searchpod:{limit}:{query.lower()}", produce, ttl=900) or []


def search_episodes(query: str, limit: int = 20) -> list[dict]:
    def produce() -> list[dict]:
        try:
            raw = client().search(query, filter="episodes", limit=limit)
        except Exception as exc:
            logger.error("Episoden-Suche fehlgeschlagen: {}", exc)
            return []
        items = []
        for entry in raw or []:
            item = mapper.normalize_episode(entry)
            if item:
                item["kind"] = "song"
                items.append(item)
        return items

    return _cached(f"searchep:{limit}:{query.lower()}", produce, ttl=900) or []


def lyrics(video_id: str) -> dict | None:
    def produce() -> dict | None:
        try:
            watch = client().get_watch_playlist(videoId=video_id, limit=1)
            browse_id = (watch or {}).get("lyrics")
            if not browse_id:
                return None
            try:
                raw = client().get_lyrics(browse_id, timestamps=True)
            except TypeError:
                raw = client().get_lyrics(browse_id)
        except Exception as exc:
            logger.debug("Songtext für {} nicht verfügbar: {}", video_id, exc)
            return None

        if not raw:
            return None

        body = raw.get("lyrics")
        if isinstance(body, str):
            return {"text": body, "source": raw.get("source") or "YouTube Music", "synced": None}

        if isinstance(body, list):
            lines = []
            for line in body:
                text = getattr(line, "text", None) or (line.get("text") if isinstance(line, dict) else None)
                start = getattr(line, "start_time", None)
                if start is None and isinstance(line, dict):
                    start = line.get("start_time")
                if text is None:
                    continue
                lines.append({"start": start, "text": text})
            return {
                "text": "\n".join(line["text"] for line in lines),
                "source": raw.get("source") or "YouTube Music",
                "synced": lines if any(line["start"] is not None for line in lines) else None,
            }

        return None

    return _cached(f"lyrics:{video_id}", produce, ttl=86400)


def account_playlists() -> list[dict]:
    """Playlists of the signed-in YouTube Music account (requires YTM_AUTH_FILE)."""
    if not is_authenticated():
        return []

    def produce() -> list[dict]:
        try:
            raw = client().get_library_playlists(limit=100)
        except Exception as exc:
            logger.warning("YTM-Bibliothek konnte nicht geladen werden: {}", exc)
            return []
        items = []
        for entry in raw or []:
            item = mapper.normalize_playlist(entry)
            if item:
                items.append(item)
        return items

    return _cached("account:playlists", produce, ttl=600) or []
