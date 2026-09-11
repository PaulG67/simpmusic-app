"""Async facade over YouTube Music that keeps local metadata snapshots fresh.

Every track/album/artist that passes through here gets written to SQLite, so
anything the user later stars or adds to a playlist can be rendered without
another round trip to YouTube.
"""

from fastapi.concurrency import run_in_threadpool

from app.db.database import session_scope
from app.db import repo
from app.ytm import client as ytm


def _persist_songs(tracks: list[dict]) -> None:
    if not tracks:
        return
    with session_scope() as session:
        repo.upsert_tracks(session, tracks)


def _persist_album(album: dict) -> None:
    with session_scope() as session:
        repo.upsert_album(session, album)
        if album.get("artist_id"):
            repo.upsert_artist(session, {"id": album["artist_id"], "name": album.get("artist", "")})
        repo.upsert_tracks(session, album.get("tracks") or [])


def _persist_artist(artist: dict) -> None:
    with session_scope() as session:
        repo.upsert_artist(session, artist)
        for album in artist.get("albums") or []:
            repo.upsert_album(session, album)
        repo.upsert_tracks(session, artist.get("top_songs") or [])


async def search(query: str, song_limit: int = 25, album_limit: int = 12, artist_limit: int = 12) -> dict:
    results = await run_in_threadpool(ytm.search_all, query, song_limit, album_limit, artist_limit)

    def persist() -> None:
        with session_scope() as session:
            repo.upsert_tracks(session, results.get("songs") or [])
            for album in results.get("albums") or []:
                repo.upsert_album(session, album)
            for artist in results.get("artists") or []:
                repo.upsert_artist(session, artist)

    await run_in_threadpool(persist)
    return results


async def search_scope(query: str, scope: str, limit: int = 25) -> list[dict]:
    items = await run_in_threadpool(ytm.search, query, scope, limit)
    if scope in ("songs", "videos"):
        await run_in_threadpool(_persist_songs, items)
    return items


async def suggestions(query: str) -> list[str]:
    return await run_in_threadpool(ytm.suggestions, query)


async def get_album(browse_id: str) -> dict | None:
    album = await run_in_threadpool(ytm.album, browse_id)
    if album:
        await run_in_threadpool(_persist_album, album)
        return album

    with session_scope() as session:
        return repo.get_album(session, browse_id)


async def get_artist(channel_id: str) -> dict | None:
    artist = await run_in_threadpool(ytm.artist, channel_id)
    if artist:
        await run_in_threadpool(_persist_artist, artist)
        return artist

    with session_scope() as session:
        return repo.get_artist(session, channel_id)


async def get_song(video_id: str) -> dict | None:
    """Local snapshot wins; YouTube is only consulted for unknown ids."""
    from app.navidrome import ids as nd_ids
    from app.navidrome.client import NavidromeClient, NavidromeError

    with session_scope() as session:
        cached = repo.get_track(session, video_id)
    if cached and cached.get("title"):
        return cached

    if nd_ids.is_navidrome(video_id):
        def load() -> dict | None:
            with NavidromeClient() as client:
                return client.get_song(nd_ids.unwrap(video_id) or "")

        try:
            track = await run_in_threadpool(load)
        except NavidromeError:
            track = None
        if track:
            await run_in_threadpool(_persist_songs, [track])
        return track or cached

    track = await run_in_threadpool(ytm.song, video_id)
    if track:
        await run_in_threadpool(_persist_songs, [track])
    return track or cached


async def get_songs(video_ids: list[str]) -> list[dict]:
    with session_scope() as session:
        known = repo.get_tracks(session, video_ids)

    missing = [video_id for video_id in video_ids if video_id not in known]
    for video_id in missing:
        track = await get_song(video_id)
        if track:
            known[video_id] = track

    return [known[video_id] for video_id in video_ids if video_id in known]


async def get_ytm_playlist(playlist_id: str, limit: int = 200) -> dict | None:
    playlist = await run_in_threadpool(ytm.playlist, playlist_id, limit)
    if playlist:
        await run_in_threadpool(_persist_songs, playlist.get("tracks") or [])
    return playlist


async def radio(video_id: str, limit: int = 30) -> list[dict]:
    tracks = await run_in_threadpool(ytm.radio, video_id, limit)
    await run_in_threadpool(_persist_songs, tracks)
    return tracks


async def similar_songs(video_id: str, limit: int = 20) -> list[dict]:
    tracks = await run_in_threadpool(ytm.similar_songs, video_id, limit)
    await run_in_threadpool(_persist_songs, tracks)
    return tracks


async def home() -> list[dict]:
    sections = await run_in_threadpool(ytm.home)
    songs = [item for section in sections for item in section["items"] if item.get("kind") == "song"]
    await run_in_threadpool(_persist_songs, songs)
    return sections


async def charts() -> list[dict]:
    sections = await run_in_threadpool(ytm.charts)
    songs = [item for section in sections for item in section["items"] if item.get("kind") == "song"]
    await run_in_threadpool(_persist_songs, songs)
    return sections


async def lyrics(video_id: str) -> dict | None:
    return await run_in_threadpool(ytm.lyrics, video_id)


# --------------------------------------------------------------------------
# Moods, genres and podcasts
# --------------------------------------------------------------------------


async def mood_categories() -> list[dict]:
    return await run_in_threadpool(ytm.mood_categories)


async def mood_playlists(params: str) -> list[dict]:
    return await run_in_threadpool(ytm.mood_playlists, params)


def _persist_podcast(podcast: dict) -> None:
    with session_scope() as session:
        repo.upsert_podcast(session, podcast)
        repo.upsert_tracks(session, podcast.get("episodes") or [])


async def get_podcast(podcast_id: str, limit: int = 100) -> dict | None:
    podcast = await run_in_threadpool(ytm.podcast, podcast_id, limit)
    if podcast:
        await run_in_threadpool(_persist_podcast, podcast)
        return podcast

    with session_scope() as session:
        return repo.get_podcast(session, podcast_id)


async def get_episode(video_id: str) -> dict | None:
    episode = await run_in_threadpool(ytm.episode, video_id)
    if episode:
        await run_in_threadpool(_persist_songs, [episode])
    return episode


async def new_episodes() -> list[dict]:
    episodes = await run_in_threadpool(ytm.new_episodes)
    await run_in_threadpool(_persist_songs, episodes)
    return episodes


async def search_podcasts(query: str, limit: int = 20) -> list[dict]:
    return await run_in_threadpool(ytm.search_podcasts, query, limit)


async def search_episodes(query: str, limit: int = 20) -> list[dict]:
    episodes = await run_in_threadpool(ytm.search_episodes, query, limit)
    await run_in_threadpool(_persist_songs, episodes)
    return episodes


async def account_playlists() -> list[dict]:
    return await run_in_threadpool(ytm.account_playlists)
