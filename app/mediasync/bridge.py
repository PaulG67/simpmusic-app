"""High-level MediaSync operations for the Music Play web API."""

from __future__ import annotations

from app.core.logging import logger
from app.mediasync import config
from app.mediasync.client import MediaSyncClient, MediaSyncError


def configured() -> bool:
    return config.configured()


def status() -> dict:
    view = config.public_view()
    if not view["configured"]:
        return {**view, "reachable": False, "version": ""}
    try:
        with MediaSyncClient() as client:
            ping = client.ping()
        return {**view, "reachable": True, "version": ping.get("version") or ""}
    except Exception as exc:
        logger.debug("MediaSync-Status: {}", exc)
        return {**view, "reachable": False, "version": "", "error": str(exc)}


def test_connection(url: str, username: str, password: str) -> dict:
    trial = {**config.load(), "url": (url or "").rstrip("/"), "username": username, "password": password}
    with MediaSyncClient(trial) as client:
        return client.ping()


def targets() -> list[dict]:
    if not config.configured():
        return []
    with MediaSyncClient() as client:
        return client.targets()


def send(track: dict, playlist_id: str | None = None, playlist_name: str | None = None) -> dict:
    if not config.configured():
        raise MediaSyncError("MediaSync ist nicht konfiguriert", 400)
    with MediaSyncClient() as client:
        return client.send(track, playlist_id=playlist_id, playlist_name=playlist_name)


def send_many(tracks: list[dict], playlist_id: str | None = None, playlist_name: str | None = None) -> dict:
    if not config.configured():
        raise MediaSyncError("MediaSync ist nicht konfiguriert", 400)
    jobs = []
    with MediaSyncClient() as client:
        for track in tracks:
            jobs.append(client.send(track, playlist_id=playlist_id, playlist_name=playlist_name))
    return {"ok": True, "jobs": jobs, "job": jobs[0] if len(jobs) == 1 else None}
