"""Normalise the many shapes ytmusicapi returns into flat internal dicts.

ytmusicapi hands back subtly different structures depending on the endpoint
(search vs. album vs. watch playlist), so everything funnels through here before
it reaches the database or the Subsonic layer.
"""

import re
from typing import Any

_SIZE_RE = re.compile(r"=w\d+-h\d+")
_DURATION_RE = re.compile(r"^\d+(:\d{1,2})+$")


def upscale_thumbnail(url: str | None, size: int = 544) -> str | None:
    if not url:
        return None
    if "googleusercontent.com" in url or "ggpht.com" in url:
        return _SIZE_RE.sub(f"=w{size}-h{size}", url)
    if "i.ytimg.com" in url:
        return re.sub(r"/(default|mqdefault|hqdefault|sddefault)\.jpg", "/maxresdefault.jpg", url)
    return url


def pick_thumbnail(raw: Any, size: int = 544) -> str | None:
    """Accept the various thumbnail containers ytmusicapi uses."""
    if not raw:
        return None
    if isinstance(raw, str):
        return upscale_thumbnail(raw, size)
    if isinstance(raw, dict):
        for key in ("thumbnails", "thumbnail"):
            if key in raw:
                return pick_thumbnail(raw[key], size)
        if "url" in raw:
            return upscale_thumbnail(raw["url"], size)
        return None
    if isinstance(raw, list) and raw:
        best = max(
            (item for item in raw if isinstance(item, dict) and item.get("url")),
            key=lambda item: item.get("width", 0) * item.get("height", 0),
            default=None,
        )
        return upscale_thumbnail(best["url"], size) if best else None
    return None


def parse_duration(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    if not _DURATION_RE.match(text):
        return 0
    seconds = 0
    for part in text.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def parse_year(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"(19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _artist_fields(raw: dict) -> tuple[str, str | None]:
    """Return (display name, primary channel id) from an entry's artist data."""
    artists = raw.get("artists")
    if isinstance(artists, list) and artists:
        names, channel = [], None
        for entry in artists:
            if isinstance(entry, dict):
                name = (entry.get("name") or "").strip()
                # ytmusicapi mixes view counts and durations into this list.
                if not name or _DURATION_RE.match(name) or name.endswith("views") or name.endswith("Aufrufe"):
                    continue
                names.append(name)
                if channel is None and entry.get("id"):
                    channel = entry["id"]
            elif isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
        if names:
            return ", ".join(dict.fromkeys(names)), channel

    for key in ("artist", "author", "channelName"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), raw.get("channelId")
        if isinstance(value, dict) and value.get("name"):
            return value["name"], value.get("id")

    return "Unbekannter Interpret", raw.get("channelId")


def _album_fields(raw: dict) -> tuple[str, str | None]:
    album = raw.get("album")
    if isinstance(album, dict):
        return (album.get("name") or "").strip(), album.get("id")
    if isinstance(album, str):
        return album.strip(), raw.get("albumId")
    return "", raw.get("albumId")


def normalize_song(raw: dict, album_hint: dict | None = None) -> dict | None:
    """album_hint carries album-level data for tracks listed inside an album."""
    if not isinstance(raw, dict):
        return None

    video_id = raw.get("videoId") or raw.get("id")
    if not video_id:
        return None

    artist, artist_id = _artist_fields(raw)
    album, album_id = _album_fields(raw)

    thumbnail = pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail"))

    if album_hint:
        album = album or album_hint.get("name") or ""
        album_id = album_id or album_hint.get("id")
        thumbnail = thumbnail or album_hint.get("thumbnail")
        if artist == "Unbekannter Interpret" and album_hint.get("artist"):
            artist = album_hint["artist"]
            artist_id = artist_id or album_hint.get("artist_id")

    duration = parse_duration(
        raw.get("duration_seconds") or raw.get("lengthSeconds") or raw.get("duration") or raw.get("length")
    )

    return {
        "id": video_id,
        "title": (raw.get("title") or "Unbekannter Titel").strip(),
        "artist": artist,
        "artist_id": artist_id,
        "album": album,
        "album_id": album_id,
        "duration": duration,
        "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "year": parse_year(raw.get("year") or (album_hint or {}).get("year")),
        "track_no": raw.get("trackNumber") or raw.get("index"),
        "disc_no": 1,
        "genre": (album_hint or {}).get("genre"),
        "explicit": bool(raw.get("isExplicit")),
        "set_video_id": raw.get("setVideoId"),
    }


def normalize_album(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    browse_id = raw.get("browseId") or raw.get("audioPlaylistId") or raw.get("id")
    if not browse_id:
        return None

    artist, artist_id = _artist_fields(raw)
    tracks = raw.get("tracks") or []

    return {
        "id": browse_id,
        "name": (raw.get("title") or raw.get("name") or "Unbekanntes Album").strip(),
        "artist": artist,
        "artist_id": artist_id,
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
        "year": parse_year(raw.get("year")),
        "song_count": raw.get("trackCount") or len(tracks),
        "duration": parse_duration(raw.get("duration_seconds") or raw.get("duration")),
        "album_type": raw.get("type"),
        "description": raw.get("description") or "",
        "audio_playlist_id": raw.get("audioPlaylistId"),
    }


def normalize_artist(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    channel_id = raw.get("channelId") or raw.get("browseId") or raw.get("id")
    name = (raw.get("artist") or raw.get("name") or raw.get("title") or "").strip()
    if not channel_id and not name:
        return None

    return {
        "id": channel_id,
        "name": name or "Unbekannter Interpret",
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
        "album_count": len((raw.get("albums") or {}).get("results") or []) if isinstance(raw.get("albums"), dict) else 0,
        "description": raw.get("description") or "",
        "subscribers": raw.get("subscribers"),
    }


def normalize_episode(raw: dict, podcast_hint: dict | None = None) -> dict | None:
    """Podcast episodes are streamed like songs, with the show as the album."""
    if not isinstance(raw, dict):
        return None

    video_id = raw.get("videoId") or raw.get("id")
    if not video_id:
        return None

    podcast = raw.get("podcast")
    if isinstance(podcast, dict):
        show, show_id = podcast.get("name") or "", podcast.get("id")
    else:
        show, show_id = (podcast_hint or {}).get("title", ""), (podcast_hint or {}).get("id")

    author = raw.get("author")
    if isinstance(author, dict):
        author_name, author_id = author.get("name") or show, author.get("id")
    else:
        author_name, author_id = (podcast_hint or {}).get("author") or show, None

    return {
        "id": video_id,
        "title": (raw.get("title") or "Episode").strip(),
        "artist": author_name or "Podcast",
        "artist_id": author_id,
        "album": show,
        "album_id": None,
        "podcast_id": show_id or (podcast_hint or {}).get("id"),
        "duration": parse_duration(raw.get("duration_seconds") or raw.get("duration")),
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail"))
        or (podcast_hint or {}).get("thumbnail"),
        "year": parse_year(raw.get("date")),
        "track_no": None,
        "disc_no": 1,
        "genre": "Podcast",
        "explicit": False,
        "description": raw.get("description") or "",
        "published": raw.get("date") or "",
        "is_episode": True,
    }


def normalize_podcast(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    podcast_id = raw.get("podcastId") or raw.get("playlistId") or raw.get("browseId") or raw.get("id")
    if not podcast_id:
        return None

    author = raw.get("author")
    if isinstance(author, dict):
        author_name = author.get("name") or ""
    elif isinstance(author, list) and author:
        author_name = author[0].get("name", "") if isinstance(author[0], dict) else str(author[0])
    else:
        author_name = str(author or "")

    return {
        "id": podcast_id,
        "title": (raw.get("title") or raw.get("name") or "Podcast").strip(),
        "author": author_name,
        "description": raw.get("description") or "",
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
    }


def normalize_playlist(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    playlist_id = raw.get("playlistId") or raw.get("browseId") or raw.get("id")
    if not playlist_id:
        return None

    author = raw.get("author")
    if isinstance(author, list):
        owner = ", ".join(entry.get("name", "") for entry in author if isinstance(entry, dict)).strip(", ")
    elif isinstance(author, dict):
        owner = author.get("name", "")
    else:
        owner = str(author or "YouTube Music")

    return {
        "id": playlist_id,
        "name": (raw.get("title") or raw.get("name") or "Playlist").strip(),
        "owner": owner or "YouTube Music",
        "description": raw.get("description") or "",
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
        "song_count": raw.get("trackCount") or raw.get("count") or len(raw.get("tracks") or []),
        "duration": parse_duration(raw.get("duration_seconds") or raw.get("duration")),
    }
