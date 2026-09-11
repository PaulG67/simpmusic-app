"""High-level Navidrome operations used by the web API and the streamer."""

from __future__ import annotations

import threading

from fastapi.concurrency import run_in_threadpool

from app.core.logging import logger
from app.db import repo
from app.db.database import session_scope
from app.navidrome import config
from app.navidrome import ids as nd_ids
from app.navidrome import match as nd_match
from app.navidrome.client import NavidromeClient, NavidromeError
from app.navidrome.importer import get_job, start_job
from app.services import catalog

_warmed = False
_warm_lock = threading.Lock()


def configured() -> bool:
    return config.configured()


def status() -> dict:
    view = config.public_view()
    if not view["configured"]:
        return {**view, "reachable": False, "version": "", "indexed": 0}
    try:
        with NavidromeClient() as client:
            ping = client.ping()
        indexed = nd_match.index_size()
        return {
            **view,
            "reachable": True,
            "version": ping.get("version") or "",
            "indexed": indexed,
        }
    except Exception as exc:
        logger.debug("Navidrome-Status: {}", exc)
        return {**view, "reachable": False, "version": "", "indexed": 0, "error": str(exc)}


def test_connection(url: str, username: str, password: str) -> dict:
    trial = {
        **config.load(),
        "url": (url or "").rstrip("/"),
        "username": username,
        "password": password,
    }
    with NavidromeClient(trial) as client:
        return client.ping()


def save_connection(payload: dict) -> dict:
    saved = config.save(payload)
    nd_match.refresh_index(force=True)
    return saved


def warm_index() -> None:
    global _warmed
    with _warm_lock:
        if _warmed or not config.configured():
            return
        _warmed = True
    threading.Thread(target=nd_match.refresh_index, kwargs={"force": True}, daemon=True, name="nd-index").start()


def reload_index() -> None:
    global _warmed
    if not config.configured():
        return
    with _warm_lock:
        _warmed = True
    threading.Thread(target=nd_match.refresh_index, kwargs={"force": True}, daemon=True, name="nd-index").start()


def enrich_items(items: list[dict], kind: str | None = None) -> list[dict]:
    if not items:
        return items
    songs = [item for item in items if (kind or item.get("kind") or "song") == "song"]
    if not songs or not config.configured():
        return items
    enriched = {id(orig): updated for orig, updated in zip(songs, nd_match.enrich(songs))}
    return [enriched.get(id(item), item) for item in items]


def navidrome_id_for(track: dict | str) -> str | None:
    if isinstance(track, str):
        if nd_ids.is_navidrome(track):
            return nd_ids.unwrap(track)
        with session_scope() as session:
            link = repo.get_navidrome_link(session, track)
        return link["navidrome_id"] if link else None

    if track.get("navidromeId"):
        return str(track["navidromeId"])
    video_id = track.get("id") or ""
    if nd_ids.is_navidrome(video_id):
        return nd_ids.unwrap(video_id)
    with session_scope() as session:
        link = repo.get_navidrome_link(session, video_id)
    return link["navidrome_id"] if link else None


def resolve_video(video_id: str) -> str | None:
    """Known mapping, or a live/index match against Navidrome."""
    found = navidrome_id_for(video_id)
    if found:
        return found
    if nd_ids.is_navidrome(video_id) or not config.configured():
        return nd_ids.unwrap(video_id)
    with session_scope() as session:
        track = repo.get_track(session, video_id)
    if not track:
        return None
    matched = nd_match.resolve(track)
    return str(matched["id"]) if matched and matched.get("id") else None


def stream_url(song_id: str) -> str:
    with NavidromeClient() as client:
        return client.stream_url(song_id)


def cover_url(cover_id: str, size: int = 544) -> str:
    with NavidromeClient() as client:
        return client.cover_url(cover_id, size)


async def library() -> dict:
    if not config.configured():
        return {"configured": False, "albums": [], "playlists": [], "recent": [], "artists": []}

    def load() -> dict:
        with NavidromeClient() as client:
            return {
                "configured": True,
                "albums": client.album_list("recent", size=24),
                "playlists": client.playlists(),
                "recent": client.album_list("newest", size=12),
                "artists": [],
            }

    try:
        return await run_in_threadpool(load)
    except NavidromeError as exc:
        logger.warning("Navidrome-Bibliothek: {}", exc)
        return {"configured": True, "error": str(exc), "albums": [], "playlists": [], "recent": [], "artists": []}


async def search(query: str, limit: int = 20) -> dict:
    if not query.strip() or not config.configured():
        return {"songs": [], "albums": [], "artists": []}

    def load() -> dict:
        with NavidromeClient() as client:
            return client.search(query, songs=limit, albums=8, artists=8)

    try:
        return await run_in_threadpool(load)
    except NavidromeError as exc:
        logger.debug("Navidrome-Suche: {}", exc)
        return {"songs": [], "albums": [], "artists": []}


async def get_album(album_id: str) -> dict | None:
    raw = nd_ids.unwrap(album_id) or album_id
    def load() -> dict | None:
        with NavidromeClient() as client:
            return client.get_album(raw)

    try:
        return await run_in_threadpool(load)
    except NavidromeError:
        return None


async def get_artist(artist_id: str) -> dict | None:
    raw = nd_ids.unwrap(artist_id) or artist_id
    def load() -> dict | None:
        with NavidromeClient() as client:
            return client.get_artist(raw)

    try:
        return await run_in_threadpool(load)
    except NavidromeError:
        return None


async def get_playlist(playlist_id: str) -> dict | None:
    raw = nd_ids.unwrap(playlist_id) or playlist_id
    def load() -> dict | None:
        with NavidromeClient() as client:
            return client.get_playlist(raw)

    try:
        return await run_in_threadpool(load)
    except NavidromeError:
        return None


def _navidrome_song_ids(tracks: list[dict]) -> list[str]:
    ids = []
    for track in nd_match.enrich(tracks, live_limit=12):
        song_id = track.get("navidromeId")
        if song_id:
            ids.append(str(song_id))
    return ids


def sync_playlist(playlist_id: int) -> str | None:
    if not config.configured():
        return None
    with session_scope() as session:
        playlist = repo.get_playlist(session, playlist_id)
        if playlist is None:
            return None
        name = playlist.name
        remote_id = playlist.navidrome_id
        tracks = repo.playlist_tracks(session, playlist_id)

    song_ids = _navidrome_song_ids(tracks)
    with NavidromeClient() as client:
        created = client.create_playlist(name, song_ids, playlist_id=remote_id)
    if created:
        with session_scope() as session:
            repo.set_playlist_navidrome_id(session, playlist_id, created)
    return created


def delete_remote_playlist(navidrome_id: str | None) -> None:
    if not navidrome_id or not config.configured():
        return
    try:
        with NavidromeClient() as client:
            client.delete_playlist(navidrome_id)
    except NavidromeError as exc:
        logger.debug("Navidrome-Playlist nicht gelöscht: {}", exc)


async def import_tracks(video_ids: list[str]) -> list[dict]:
    tracks = await catalog.get_songs(video_ids)
    by_id = {track["id"]: track for track in tracks}
    jobs = []
    for video_id in video_ids:
        track = by_id.get(video_id)
        if not track:
            continue
        jobs.append(start_job(track))
    return jobs


def job(job_id: str) -> dict | None:
    return get_job(job_id)
