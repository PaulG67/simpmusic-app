"""Normalise the many shapes ytmusicapi returns into flat internal dicts.

ytmusicapi hands back subtly different structures depending on the endpoint
(search vs. album vs. watch playlist), so everything funnels through here before
it reaches the database or the Subsonic layer.
"""

import re
from typing import Any

_SIZE_RE = re.compile(r"=w\d+-h\d+")
_DURATION_RE = re.compile(r"^\d+(:\d{1,2})+$")


def _text(value: Any, fallback: str = "") -> str:
    """Coerce ytmusicapi leftovers (None, runs, dicts) into a display string."""
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_text(part) for part in value]
        return " ".join(part for part in parts if part) or fallback
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("title") or value.get("text"), fallback)
    return str(value).strip() or fallback


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


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().split()[0].replace(",", "")
    if text.isdigit():
        return int(text)
    return None


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
                name = _text(entry.get("name"))
                if not name or _DURATION_RE.match(name) or name.endswith("views") or name.endswith("Aufrufe"):
                    continue
                names.append(name)
                if channel is None and entry.get("id"):
                    channel = _text(entry.get("id")) or None
            elif isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
        if names:
            return ", ".join(dict.fromkeys(names)), channel

    for key in ("artist", "author", "channelName"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), raw.get("channelId")
        if isinstance(value, dict) and value.get("name"):
            return _text(value.get("name")), value.get("id")

    return "Unbekannter Interpret", raw.get("channelId")


def _album_fields(raw: dict) -> tuple[str, str | None]:
    album = raw.get("album")
    if isinstance(album, dict):
        return _text(album.get("name")), album.get("id")
    if isinstance(album, str):
        return album.strip(), raw.get("albumId")
    return "", raw.get("albumId")


def normalize_song(raw: dict, album_hint: dict | None = None) -> dict | None:
    """album_hint carries album-level data for tracks listed inside an album."""
    if not isinstance(raw, dict):
        return None

    video_id = _text(raw.get("videoId") or raw.get("id"))
    if not video_id:
        return None

    artist, artist_id = _artist_fields(raw)
    album, album_id = _album_fields(raw)

    thumbnail = pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail"))

    if album_hint:
        album = album or _text(album_hint.get("name"))
        album_id = album_id or album_hint.get("id")
        thumbnail = thumbnail or album_hint.get("thumbnail")
        if artist == "Unbekannter Interpret" and album_hint.get("artist"):
            artist = _text(album_hint.get("artist"), artist)
            artist_id = artist_id or album_hint.get("artist_id")

    duration = parse_duration(
        raw.get("duration_seconds") or raw.get("lengthSeconds") or raw.get("duration") or raw.get("length")
    )

    return {
        "id": video_id,
        "title": _text(raw.get("title"), "Unbekannter Titel"),
        "artist": _text(artist, "Unbekannter Interpret"),
        "artist_id": artist_id,
        "album": _text(album),
        "album_id": album_id,
        "duration": duration,
        "thumbnail": thumbnail or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "year": parse_year(raw.get("year") or (album_hint or {}).get("year")),
        "track_no": _int(raw.get("trackNumber") or raw.get("index")),
        "disc_no": 1,
        "genre": _text((album_hint or {}).get("genre")) or None,
        "explicit": bool(raw.get("isExplicit")),
        "set_video_id": raw.get("setVideoId") if isinstance(raw.get("setVideoId"), str) else None,
    }


def normalize_album(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    browse_id = _text(raw.get("browseId") or raw.get("audioPlaylistId") or raw.get("id"))
    if not browse_id:
        return None

    artist, artist_id = _artist_fields(raw)
    tracks = raw.get("tracks") or []

    return {
        "id": browse_id,
        "name": _text(raw.get("title") or raw.get("name"), "Unbekanntes Album"),
        "artist": artist,
        "artist_id": artist_id,
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
        "year": parse_year(raw.get("year")),
        "song_count": _int(raw.get("trackCount") or raw.get("count")) or len(tracks),
        "duration": parse_duration(raw.get("duration_seconds") or raw.get("duration")),
        "album_type": _text(raw.get("type")) or None,
        "description": _text(raw.get("description")),
        "audio_playlist_id": raw.get("audioPlaylistId"),
    }


def normalize_artist(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    channel_id = _text(raw.get("channelId") or raw.get("browseId") or raw.get("id")) or None
    name = _text(raw.get("artist") or raw.get("name") or raw.get("title"))
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

    playlist_id = _text(raw.get("playlistId") or raw.get("browseId") or raw.get("id"))
    if playlist_id.startswith("VL"):
        playlist_id = playlist_id[2:]
    if not playlist_id:
        return None

    author = raw.get("author")
    if isinstance(author, list):
        owner = ", ".join(
            filter(
                None,
                (_text(entry.get("name") if isinstance(entry, dict) else entry) for entry in author),
            )
        )
    elif isinstance(author, dict):
        owner = _text(author.get("name"))
    else:
        owner = _text(author, "YouTube Music")

    return {
        "id": playlist_id,
        "name": _text(raw.get("title") or raw.get("name"), "Playlist"),
        "owner": owner or "YouTube Music",
        "description": _text(raw.get("description")),
        "thumbnail": pick_thumbnail(raw.get("thumbnails") or raw.get("thumbnail")),
        "song_count": _int(raw.get("trackCount") or raw.get("count")) or len(raw.get("tracks") or []),
        "duration": parse_duration(raw.get("duration_seconds") or raw.get("duration")),
    }
