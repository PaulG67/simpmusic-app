"""Download a YouTube Music track into the Navidrome music folder."""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp

from app.core.logging import logger
from app.core.settings import settings
from app.db import repo
from app.db.database import session_scope
from app.navidrome import config
from app.navidrome.client import NavidromeClient
from app.navidrome import match as nd_match
from app.ytm import streams

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_inflight: dict[str, str] = {}


def _safe(value: str | None, fallback: str = "Unbekannt", limit: int = 80) -> str:
    text = _UNSAFE.sub("_", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:limit]


def _destination(track: dict) -> Path:
    artist = _safe(track.get("artist"), "Unbekannt")
    album = _safe(track.get("album"), "Singles")
    title = _safe(track.get("title"), track.get("id") or "Titel")
    number = track.get("track_no")
    filename = f"{int(number):02d} - {title}.m4a" if number else f"{title}.m4a"
    return config.import_root() / artist / album / filename


def _ydl_options(output_template: str) -> dict:
    options = {
        "format": streams.FORMAT_SELECTOR,
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "writethumbnail": True,
        "embedthumbnail": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
        "postprocessor_args": {"ffmpeg": ["-movflags", "+faststart"]},
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


def _write_ffmpeg_tags(source: Path, dest: Path, track: dict) -> None:
    if shutil.which("ffmpeg") is None:
        shutil.copy2(source, dest)
        return
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c",
        "copy",
        "-metadata",
        f"title={track.get('title') or ''}",
        "-metadata",
        f"artist={track.get('artist') or ''}",
        "-metadata",
        f"album={track.get('album') or ''}",
        "-metadata",
        f"album_artist={track.get('artist') or ''}",
        "-metadata",
        f"date={track.get('year') or ''}",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    if track.get("track_no"):
        index = command.index("-movflags")
        command[index:index] = ["-metadata", f"track={track['track_no']}"]
    try:
        import subprocess

        subprocess.run(command, check=True, timeout=120)
    except Exception as exc:
        logger.warning("Metadaten für {} nicht gesetzt, kopiere Datei: {}", dest.name, exc)
        shutil.copy2(source, dest)


def _wait_for_scan(client: NavidromeClient, timeout: int = 90) -> None:
    client.start_scan()
    deadline = time.time() + timeout
    saw_scan = False
    while time.time() < deadline:
        status = client.scan_status()
        if status.get("scanning"):
            saw_scan = True
        elif saw_scan:
            return
        time.sleep(1.5)
    # Watcher-based libraries may never report scanning=true.
    time.sleep(2)


def _locate(track: dict) -> dict | None:
    nd_match.refresh_index(force=True)
    found = nd_match.find_in_index(track) or nd_match.find_live(track)
    return found


def import_track(track: dict) -> dict:
    video_id = track.get("id") or ""
    if not video_id:
        raise RuntimeError("Titel ohne YouTube-Id")
    if video_id.startswith("nd:"):
        return {"navidromeId": video_id[3:], "path": "", "skipped": True}

    existing = nd_match.resolve(track)
    if existing and existing.get("id"):
        return {"navidromeId": existing["id"], "path": existing.get("path") or "", "skipped": True}

    if not config.configured():
        raise RuntimeError("Navidrome ist nicht konfiguriert")
    if not config.music_dir_writable():
        raise RuntimeError(
            f"Musikordner nicht beschreibbar: {config.music_dir()}. "
            "Denselben Ordner wie Navidrome nach /music einhängen."
        )

    dest = _destination(track)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 1024:
        logger.info("Datei existiert bereits, überspringe Download: {}", dest)
        with NavidromeClient() as client:
            _wait_for_scan(client)
            found = _locate(track)
        if found and found.get("id"):
            with session_scope() as session:
                repo.upsert_navidrome_link(
                    session,
                    video_id,
                    str(found["id"]),
                    title=track.get("title") or "",
                    artist=track.get("artist") or "",
                    path=str(dest),
                )
            return {"navidromeId": str(found["id"]), "path": str(dest), "skipped": True}

    tmp_dir = settings.audio_cache_dir / "import"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Importiere {} nach {}", video_id, dest)
    try:
        with yt_dlp.YoutubeDL(_ydl_options(str(tmp_dir / f"{video_id}.%(ext)s"))) as ydl:
            ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=True)
    except Exception as exc:
        raise RuntimeError(f"Download fehlgeschlagen: {exc}") from exc

    produced = next((path for path in tmp_dir.glob(f"{video_id}*") if path.suffix.lower() in {".m4a", ".mp4", ".mp3"}), None)
    if produced is None:
        raise RuntimeError("yt-dlp hat keine Audiodatei erzeugt")

    _write_ffmpeg_tags(produced, dest, track)
    for leftover in tmp_dir.glob(f"{video_id}*"):
        leftover.unlink(missing_ok=True)

    with NavidromeClient() as client:
        _wait_for_scan(client)
        found = _locate(track)
        if found is None:
            time.sleep(3)
            found = _locate(track)

    if not found or not found.get("id"):
        raise RuntimeError(
            "Datei liegt in der Bibliothek, Navidrome hat sie aber noch nicht indiziert. "
            "Später erneut versuchen oder in Navidrome einen Scan starten."
        )

    with session_scope() as session:
        repo.upsert_navidrome_link(
            session,
            video_id,
            str(found["id"]),
            title=track.get("title") or "",
            artist=track.get("artist") or "",
            path=str(dest),
        )
    return {"navidromeId": str(found["id"]), "path": str(dest), "skipped": False}


def job_snapshot(job: dict) -> dict:
    return {
        "id": job["id"],
        "videoId": job["videoId"],
        "status": job["status"],
        "error": job.get("error"),
        "navidromeId": job.get("navidromeId"),
        "title": job.get("title") or "",
    }


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return job_snapshot(job) if job else None


def _run(job_id: str, track: dict) -> None:
    video_id = track.get("id") or ""
    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
        result = import_track(track)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["navidromeId"] = result.get("navidromeId")
    except Exception as exc:
        logger.error("Navidrome-Import von {} fehlgeschlagen: {}", video_id, exc)
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)
    finally:
        with _jobs_lock:
            _inflight.pop(video_id, None)


def start_job(track: dict) -> dict:
    video_id = track.get("id") or ""
    with _jobs_lock:
        existing_id = _inflight.get(video_id)
        if existing_id and existing_id in _jobs:
            return job_snapshot(_jobs[existing_id])
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "videoId": video_id,
            "status": "queued",
            "error": None,
            "navidromeId": None,
            "title": track.get("title") or "",
        }
        _jobs[job_id] = job
        _inflight[video_id] = job_id

    thread = threading.Thread(target=_run, args=(job_id, track), daemon=True, name=f"nd-import-{video_id}")
    thread.start()
    return job_snapshot(job)
