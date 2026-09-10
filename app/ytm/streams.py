"""Audio resolution and on-disk caching.

YouTube hands out short-lived, signed media URLs, so every playback needs a
fresh resolve unless we already hold the bytes on disk. Preference goes to the
AAC/m4a rendition because iOS cannot play Opus-in-WebM, which is what YouTube
otherwise serves for music.
"""

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yt_dlp
from sqlalchemy import select

from app.core.logging import logger
from app.core.settings import settings
from app.db.database import session_scope
from app.db.models import StreamCacheEntry, utcnow
from app.ytm.cache import TTLCache

CONTENT_TYPES = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "webm": "audio/webm",
    "opus": "audio/ogg",
    "mp3": "audio/mpeg",
}

FORMAT_SELECTOR = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"

_url_cache = TTLCache(ttl=3600, max_items=500)
_resolve_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass
class StreamSource:
    video_id: str
    url: str
    ext: str
    content_type: str
    suffix: str
    filesize: int | None
    bitrate: int
    acodec: str
    needs_transcode: bool


def _lock_for(video_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _resolve_locks.get(video_id)
        if lock is None:
            lock = threading.Lock()
            _resolve_locks[video_id] = lock
        return lock


def _ydl_options() -> dict:
    options = {
        "format": FORMAT_SELECTOR,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 2,
        "cachedir": str(settings.audio_cache_dir.parent / "ytdlp"),
    }
    if settings.ytdlp_cookie_file and Path(settings.ytdlp_cookie_file).exists():
        options["cookiefile"] = settings.ytdlp_cookie_file
    if settings.ytdlp_player_clients:
        clients = [c.strip() for c in settings.ytdlp_player_clients.split(",") if c.strip()]
        options["extractor_args"] = {"youtube": {"player_client": clients}}
    return options


def _url_lifetime(url: str) -> int:
    """Seconds the signed URL stays usable, minus a safety margin."""
    try:
        expire = parse_qs(urlparse(url).query).get("expire", [None])[0]
        if expire:
            remaining = int(expire) - int(time.time()) - settings.stream_url_margin
            return max(remaining, 60)
    except (TypeError, ValueError):
        pass
    return 1800


def _pick_format(info: dict) -> dict | None:
    requested = info.get("requested_formats")
    if requested:
        for fmt in requested:
            if fmt.get("acodec") not in (None, "none"):
                return fmt
        return requested[0]
    if info.get("url"):
        return info
    formats = [f for f in info.get("formats") or [] if f.get("acodec") not in (None, "none") and f.get("url")]
    if not formats:
        return None
    formats.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or 0), reverse=True)
    return formats[0]


def resolve_source(video_id: str) -> StreamSource | None:
    """Blocking; call from a worker thread."""
    cached = _url_cache.get(video_id)
    if cached is not None:
        return cached

    with _lock_for(video_id):
        cached = _url_cache.get(video_id)
        if cached is not None:
            return cached

        try:
            with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
                info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
        except Exception as exc:
            logger.error("yt-dlp konnte {} nicht auflösen: {}", video_id, exc)
            return None

        fmt = _pick_format(info or {})
        if not fmt or not fmt.get("url"):
            logger.error("Kein abspielbares Audioformat für {}", video_id)
            return None

        ext = (fmt.get("ext") or "m4a").lower()
        acodec = (fmt.get("acodec") or "").lower()
        needs_transcode = settings.transcode_to_aac and not (
            ext in ("m4a", "mp4") or acodec.startswith("mp4a") or acodec.startswith("aac")
        )

        source = StreamSource(
            video_id=video_id,
            url=fmt["url"],
            ext=ext,
            content_type=CONTENT_TYPES.get(ext, "audio/mpeg"),
            suffix=ext,
            filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
            bitrate=int(fmt.get("abr") or 128),
            acodec=acodec,
            needs_transcode=needs_transcode,
        )

        _url_cache.set(video_id, source, ttl=_url_lifetime(source.url))
        return source


# --------------------------------------------------------------------------
# Disk cache
# --------------------------------------------------------------------------


def cached_entry(video_id: str) -> dict | None:
    if not settings.cache_enabled:
        return None

    with session_scope() as session:
        row = session.get(StreamCacheEntry, video_id)
        if row is None:
            return None
        path = settings.audio_cache_dir / row.filename
        if not path.exists():
            session.delete(row)
            return None
        row.last_access = utcnow()
        return {
            "path": path,
            "content_type": row.content_type,
            "suffix": row.suffix,
            "size": row.size or path.stat().st_size,
            "bitrate": row.bitrate,
        }


def register_cached(video_id: str, path: Path, content_type: str, suffix: str, bitrate: int) -> None:
    size = path.stat().st_size
    with session_scope() as session:
        row = session.get(StreamCacheEntry, video_id)
        if row is None:
            row = StreamCacheEntry(id=video_id)
            session.add(row)
        row.filename = path.name
        row.content_type = content_type
        row.suffix = suffix
        row.size = size
        row.bitrate = bitrate
        row.last_access = utcnow()
    enforce_cache_limit()


def enforce_cache_limit() -> None:
    limit_bytes = int(settings.cache_max_gb * 1024**3)
    if limit_bytes <= 0:
        return

    with session_scope() as session:
        rows = list(session.scalars(select(StreamCacheEntry).order_by(StreamCacheEntry.last_access.asc())).all())
        total = sum(row.size or 0 for row in rows)
        for row in rows:
            if total <= limit_bytes:
                break
            path = settings.audio_cache_dir / row.filename
            path.unlink(missing_ok=True)
            total -= row.size or 0
            session.delete(row)
            logger.debug("Cache-Eintrag {} entfernt", row.id)


def clear_disk_cache() -> None:
    with session_scope() as session:
        for row in session.scalars(select(StreamCacheEntry)).all():
            (settings.audio_cache_dir / row.filename).unlink(missing_ok=True)
            session.delete(row)


def cache_stats() -> dict:
    with session_scope() as session:
        rows = list(session.scalars(select(StreamCacheEntry)).all())
    return {
        "tracks": len(rows),
        "bytes": sum(row.size or 0 for row in rows),
        "limit_bytes": int(settings.cache_max_gb * 1024**3),
    }


# --------------------------------------------------------------------------
# Transcoding fallback
# --------------------------------------------------------------------------


def transcode_to_m4a(source: StreamSource) -> Path | None:
    """Only used when YouTube offers no AAC rendition; iOS rejects Opus/WebM."""
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg fehlt, Opus kann nicht nach AAC gewandelt werden")
        return None

    target = settings.audio_cache_dir / f"{source.video_id}.m4a"
    partial = target.with_suffix(".m4a.part")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", source.url,
        "-vn",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(partial),
    ]

    logger.info("Wandle {} nach AAC (kein m4a von YouTube verfügbar)", source.video_id)
    try:
        subprocess.run(command, check=True, timeout=600)
    except Exception as exc:
        logger.error("Transkodierung von {} fehlgeschlagen: {}", source.video_id, exc)
        partial.unlink(missing_ok=True)
        return None

    partial.replace(target)
    register_cached(source.video_id, target, "audio/mp4", "m4a", 192)
    return target
