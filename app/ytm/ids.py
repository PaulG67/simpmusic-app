"""Stable Subsonic ids for YouTube Music entities.

Subsonic ids travel through query strings and client databases, so they must be
URL-safe and must not change between restarts. Every id is `<prefix>_<payload>`
and the prefixes never contain an underscore, so splitting on the first one is
unambiguous even though YouTube ids may contain `-` and `_`.
"""

import base64

SONG = "t"
ALBUM = "al"
ARTIST = "ar"
PLAYLIST = "pl"
YT_PLAYLIST = "yp"
PODCAST = "pc"
MOOD = "md"
COVER = "cv"

_PREFIXES = {SONG, ALBUM, ARTIST, PLAYLIST, YT_PLAYLIST, PODCAST, MOOD, COVER}


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")


def song_id(video_id: str) -> str:
    return f"{SONG}_{video_id}"


def album_id(browse_id: str) -> str:
    return f"{ALBUM}_{browse_id}"


def artist_id(channel_id: str | None, name: str = "") -> str:
    if channel_id:
        return f"{ARTIST}_{channel_id}"
    return f"{ARTIST}_n{_b64(name or 'Unbekannt')}"


def playlist_id(local_id: int) -> str:
    return f"{PLAYLIST}_{local_id}"


def yt_playlist_id(ytm_playlist_id: str) -> str:
    return f"{YT_PLAYLIST}_{ytm_playlist_id}"


def podcast_id(playlist_id: str) -> str:
    return f"{PODCAST}_{playlist_id}"


def mood_id(params: str) -> str:
    """Mood/genre `params` are opaque YouTube tokens and may contain padding."""
    return f"{MOOD}_{_b64(params)}"


def mood_payload(value: str) -> str | None:
    kind, payload = parse(value)
    if kind != MOOD:
        return None
    try:
        return _unb64(payload)
    except Exception:
        return None


def cover_id(url: str) -> str:
    return f"{COVER}_{_b64(url)}"


def parse(value: str | None) -> tuple[str, str]:
    """Return (kind, payload). Unknown input yields ("", original)."""
    if not value:
        return "", ""
    prefix, _, payload = value.partition("_")
    if prefix in _PREFIXES and payload:
        return prefix, payload
    return "", value


def artist_payload(value: str) -> tuple[str | None, str | None]:
    """Split an artist id into (channel_id, fallback_name)."""
    kind, payload = parse(value)
    if kind != ARTIST:
        return None, None
    if payload.startswith("n"):
        try:
            return None, _unb64(payload[1:])
        except Exception:
            return None, None
    return payload, None


def cover_payload(value: str) -> str | None:
    kind, payload = parse(value)
    if kind != COVER:
        return None
    try:
        return _unb64(payload)
    except Exception:
        return None
