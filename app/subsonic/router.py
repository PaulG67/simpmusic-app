"""Subsonic / OpenSubsonic REST API.

Clients call `/rest/<action>` or `/rest/<action>.view`, so a single dispatching
route covers the whole surface. Every handler returns the *body* of the
`subsonic-response`; the envelope, format negotiation and error wrapping happen
in `response.render`.

The library exposed here is intentionally the user's curated collection
(favourites, playlists, history) rather than all of YouTube Music, because
clients like Amperfy sync the entire library up front. Discovery happens
through `search3`, which queries YouTube Music live.
"""

import random
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.settings import APP_VERSION, settings
from app.db import repo
from app.db.database import session_scope
from app.services import catalog
from app.subsonic import media
from app.subsonic.auth import authenticate
from app.subsonic.entities import (
    LibraryContext,
    album_entity,
    artist_entity,
    directory_child_album,
    episode_entity,
    playlist_entity,
    podcast_channel_entity,
    song_entity,
)
from app.subsonic.response import (
    ERROR_GENERIC,
    ERROR_MISSING_PARAM,
    ERROR_NOT_FOUND,
    SubsonicError,
    iso,
    render,
    render_error,
)
from app.ytm import ids
from app.ytm import client as ytm

router = APIRouter()

IGNORED_ARTICLES = "The El La Los Las Le Les Der Die Das Ein Eine"
CHARTS_PLAYLIST = "CHARTS"


class Params:
    def __init__(self, data: dict[str, list[str]]):
        self._data = data

    def get(self, name: str, default: str | None = None) -> str | None:
        values = self._data.get(name)
        return values[0] if values else default

    def all(self, name: str) -> list[str]:
        return self._data.get(name, [])

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None or value == "":
            raise SubsonicError(ERROR_MISSING_PARAM, f"Erforderlicher Parameter '{name}' fehlt")
        return value

    def int(self, name: str, default: int) -> int:
        try:
            return int(self.get(name) or default)
        except (TypeError, ValueError):
            return default

    def bool(self, name: str, default: bool = False) -> bool:
        value = self.get(name)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes")


def _action(request: Request) -> str:
    """Normalised endpoint name, e.g. `getalbumlist2` for `/rest/getAlbumList2.view`."""
    return getattr(request.state, "subsonic_action", "")


async def _collect_params(request: Request) -> Params:
    data: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        data.setdefault(key, []).append(value)

    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:
            form = {}
        for key, value in form.multi_items() if hasattr(form, "multi_items") else form.items():
            data.setdefault(key, []).append(str(value))

    return Params(data)


# --------------------------------------------------------------------------
# Shared loaders
# --------------------------------------------------------------------------


async def _load_song(subsonic_id: str) -> dict | None:
    kind, payload = ids.parse(subsonic_id)
    if kind != ids.SONG:
        return None
    return await catalog.get_song(payload)


async def _load_album(subsonic_id: str) -> dict | None:
    kind, payload = ids.parse(subsonic_id)
    if kind != ids.ALBUM:
        return None
    return await catalog.get_album(payload)


async def _load_artist(subsonic_id: str) -> dict | None:
    """Artists either exist on YouTube Music or are implied by library tracks."""
    channel_id, fallback_name = ids.artist_payload(subsonic_id)

    if channel_id:
        artist = await catalog.get_artist(channel_id)
        if artist:
            return artist

    with session_scope() as session:
        tracks = [
            track
            for track in repo.library_tracks(session)
            if (channel_id and track.get("artist_id") == channel_id)
            or (fallback_name and track.get("artist") == fallback_name)
        ]
        cached = repo.get_artist(session, channel_id) if channel_id else None

        albums: dict[str, dict] = {}
        for track in tracks:
            album_id = track.get("album_id")
            if not album_id or album_id in albums:
                continue
            albums[album_id] = repo.get_album(session, album_id) or {
                "id": album_id,
                "name": track.get("album") or "Unbekanntes Album",
                "artist": track.get("artist") or "",
                "artist_id": track.get("artist_id"),
                "thumbnail": track.get("thumbnail"),
                "year": track.get("year"),
            }

    if not tracks and not cached:
        return None

    return {
        "id": channel_id,
        "name": (cached or {}).get("name") or fallback_name or (tracks[0]["artist"] if tracks else "Unbekannt"),
        "thumbnail": (cached or {}).get("thumbnail") or (tracks[0]["thumbnail"] if tracks else None),
        "albums": list(albums.values()),
        "album_count": len(albums),
        "top_songs": tracks,
    }


async def _load_playlist(subsonic_id: str) -> tuple[dict, list[dict]] | None:
    kind, payload = ids.parse(subsonic_id)

    if kind == ids.PLAYLIST:
        try:
            local_id = int(payload)
        except ValueError:
            return None
        with session_scope() as session:
            playlist = repo.get_playlist(session, local_id)
            if playlist is None:
                return None
            summary = repo.playlist_summary(session, playlist)
            tracks = repo.playlist_tracks(session, local_id)
        return summary, tracks

    if kind == ids.YT_PLAYLIST:
        if payload == CHARTS_PLAYLIST:
            sections = await catalog.charts()
            tracks = [item for section in sections for item in section["items"] if item.get("kind") == "song"]
            summary = {
                "id": CHARTS_PLAYLIST,
                "name": f"Charts {settings.ytm_location}",
                "description": "Aktuelle Top-Titel von YouTube Music",
                "song_count": len(tracks),
                "duration": sum(track["duration"] for track in tracks),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            return summary, tracks

        playlist = await catalog.get_ytm_playlist(payload)
        if playlist is None:
            return None
        return playlist, playlist.get("tracks") or []

    return None


def _discovery_playlists() -> list[dict]:
    return [
        {
            "id": CHARTS_PLAYLIST,
            "name": f"Charts {settings.ytm_location}",
            "description": "Aktuelle Top-Titel von YouTube Music",
            "song_count": 0,
            "duration": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    ]


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------


async def h_ping(request, p, user):
    return {}


async def h_get_license(request, p, user):
    return {"license": {"valid": True, "email": f"{user}@music-play.local", "licenseExpires": "2099-12-31T00:00:00.000Z"}}


async def h_get_open_subsonic_extensions(request, p, user):
    return {
        "openSubsonicExtensions": [
            {"name": "formPost", "versions": [1]},
            {"name": "songLyrics", "versions": [1]},
            {"name": "transcodeOffset", "versions": [1]},
        ]
    }


async def h_get_scan_status(request, p, user):
    return {"scanStatus": {"scanning": False, "count": 0}}


async def h_start_scan(request, p, user):
    ytm.clear_cache()
    return {"scanStatus": {"scanning": False, "count": 0}}


async def h_get_user(request, p, user):
    return {
        "user": {
            "username": user,
            "email": f"{user}@music-play.local",
            "scrobblingEnabled": True,
            "adminRole": False,
            "settingsRole": False,
            "downloadRole": True,
            "uploadRole": False,
            "playlistRole": True,
            "coverArtRole": True,
            "commentRole": False,
            "podcastRole": True,
            "streamRole": True,
            "jukeboxRole": False,
            "shareRole": False,
            "folder": [0],
        }
    }


async def h_get_users(request, p, user):
    body = await h_get_user(request, p, user)
    return {"users": {"user": [body["user"]]}}


# --------------------------------------------------------------------------
# Browsing
# --------------------------------------------------------------------------


async def h_get_music_folders(request, p, user):
    return {"musicFolders": {"musicFolder": [{"id": 0, "name": "YouTube Music"}]}}


def _index_artists(artists: list[dict], ctx: LibraryContext) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for artist in artists:
        entity = artist_entity(artist, ctx)
        first = (entity["name"][:1] or "#").upper()
        buckets.setdefault(first if first.isalpha() else "#", []).append(entity)
    return [{"name": key, "artist": buckets[key]} for key in sorted(buckets)]


async def h_get_indexes(request, p, user):
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        artists = repo.library_artists(session)
    return {
        "indexes": {
            "lastModified": int(time.time() * 1000),
            "ignoredArticles": IGNORED_ARTICLES,
            "index": _index_artists(artists, ctx),
        }
    }


async def h_get_artists(request, p, user):
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        artists = repo.library_artists(session)
    return {
        "artists": {
            "ignoredArticles": IGNORED_ARTICLES,
            "index": _index_artists(artists, ctx),
        }
    }


async def h_get_artist(request, p, user):
    artist = await _load_artist(p.require("id"))
    if artist is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Interpret nicht gefunden")
    with session_scope() as session:
        ctx = LibraryContext.load(session)
    return {"artist": artist_entity(artist, ctx, with_albums=True)}


async def h_get_album(request, p, user):
    album = await _load_album(p.require("id"))
    if album is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Album nicht gefunden")
    with session_scope() as session:
        ctx = LibraryContext.load(session)
    return {"album": album_entity(album, ctx, with_songs=True)}


async def h_get_song(request, p, user):
    track = await _load_song(p.require("id"))
    if track is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Titel nicht gefunden")
    with session_scope() as session:
        ctx = LibraryContext.load(session)
    return {"song": song_entity(track, ctx)}


async def h_get_music_directory(request, p, user):
    directory_id = p.require("id")
    kind, _ = ids.parse(directory_id)

    with session_scope() as session:
        ctx = LibraryContext.load(session)

    if kind == ids.ARTIST:
        artist = await _load_artist(directory_id)
        if artist is None:
            raise SubsonicError(ERROR_NOT_FOUND, "Interpret nicht gefunden")
        children = [directory_child_album(album, ctx) for album in artist.get("albums") or []]
        return {"directory": {"id": directory_id, "name": artist["name"], "child": children}}

    if kind == ids.ALBUM:
        album = await _load_album(directory_id)
        if album is None:
            raise SubsonicError(ERROR_NOT_FOUND, "Album nicht gefunden")
        children = [song_entity(track, ctx) for track in album.get("tracks") or []]
        parent = ids.artist_id(album.get("artist_id"), album.get("artist", ""))
        return {"directory": {"id": directory_id, "parent": parent, "name": album["name"], "child": children}}

    raise SubsonicError(ERROR_NOT_FOUND, "Verzeichnis nicht gefunden")


async def h_get_genres(request, p, user):
    with session_scope() as session:
        genres = repo.library_genres(session)
    return {"genres": {"genre": [{"value": name, "songCount": count, "albumCount": 0} for name, count in genres]}}


async def h_get_artist_info(request, p, user):
    artist = await _load_artist(p.require("id"))
    if artist is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Interpret nicht gefunden")

    with session_scope() as session:
        ctx = LibraryContext.load(session)

    similar = [artist_entity(item, ctx) for item in (artist.get("related") or [])[: p.int("count", 20)]]
    image = artist.get("thumbnail")
    info = {
        "biography": artist.get("description") or "",
        "smallImageUrl": image,
        "mediumImageUrl": image,
        "largeImageUrl": image,
        "similarArtist": similar,
    }
    key = "artistInfo2" if _action(request).endswith("2") else "artistInfo"
    return {key: info}


async def h_get_album_info(request, p, user):
    album = await _load_album(p.require("id"))
    if album is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Album nicht gefunden")
    image = album.get("thumbnail")
    info = {
        "notes": album.get("description") or "",
        "smallImageUrl": image,
        "mediumImageUrl": image,
        "largeImageUrl": image,
    }
    key = "albumInfo2" if _action(request).endswith("2") else "albumInfo"
    return {key: info}


async def h_get_similar_songs(request, p, user):
    kind, payload = ids.parse(p.require("id"))
    count = p.int("count", 50)

    if kind == ids.SONG:
        tracks = await catalog.similar_songs(payload, count)
    elif kind == ids.ARTIST:
        artist = await _load_artist(p.require("id"))
        tracks = (artist or {}).get("top_songs", [])[:count]
    elif kind == ids.ALBUM:
        album = await _load_album(p.require("id"))
        seed = (album or {}).get("tracks") or []
        tracks = await catalog.similar_songs(seed[0]["id"], count) if seed else []
    else:
        tracks = []

    with session_scope() as session:
        ctx = LibraryContext.load(session)

    key = "similarSongs2" if _action(request).endswith("2") else "similarSongs"
    return {key: {"song": [song_entity(track, ctx) for track in tracks]}}


async def h_get_top_songs(request, p, user):
    name = p.get("artist") or ""
    count = p.int("count", 50)
    tracks: list[dict] = []

    if name:
        artists = await catalog.search_scope(name, "artists", 1)
        if artists and artists[0].get("id"):
            artist = await catalog.get_artist(artists[0]["id"])
            tracks = (artist or {}).get("top_songs", [])[:count]
        if not tracks:
            tracks = (await catalog.search_scope(name, "songs", count))[:count]

    with session_scope() as session:
        ctx = LibraryContext.load(session)
    return {"topSongs": {"song": [song_entity(track, ctx) for track in tracks]}}


# --------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------


async def _album_list(p: Params) -> list[dict]:
    list_type = p.get("type", "alphabeticalByName")
    size = min(p.int("size", 20), 500)
    offset = p.int("offset", 0)

    with session_scope() as session:
        if list_type == "starred":
            albums = repo.starred_albums(session)
        else:
            albums = repo.library_albums(session)

        if list_type == "newest":
            albums.sort(key=lambda a: a.get("created_at") or datetime.min, reverse=True)
        elif list_type == "alphabeticalByArtist":
            albums.sort(key=lambda a: ((a.get("artist") or "").lower(), (a.get("name") or "").lower()))
        elif list_type == "byYear":
            start, end = p.int("fromYear", 0), p.int("toYear", 9999)
            low, high = min(start, end), max(start, end)
            albums = [a for a in albums if a.get("year") and low <= a["year"] <= high]
            albums.sort(key=lambda a: a.get("year") or 0, reverse=start > end)
        elif list_type == "byGenre":
            genre = (p.get("genre") or "").lower()
            albums = [a for a in albums if (a.get("genre") or "").lower() == genre]
        elif list_type == "random":
            random.shuffle(albums)
        elif list_type in ("frequent", "recent", "highest"):
            plays = repo.play_counts(session)
            tracks_by_album: dict[str, int] = {}
            for track in repo.library_tracks(session):
                if track.get("album_id"):
                    tracks_by_album[track["album_id"]] = tracks_by_album.get(track["album_id"], 0) + plays.get(track["id"], 0)
            albums.sort(key=lambda a: tracks_by_album.get(a["id"], 0), reverse=True)
        else:
            albums.sort(key=lambda a: (a.get("name") or "").lower())

    return albums[offset : offset + size]


async def h_get_album_list(request, p, user):
    albums = await _album_list(p)
    with session_scope() as session:
        ctx = LibraryContext.load(session)
    key = "albumList2" if _action(request).endswith("2") else "albumList"
    return {key: {"album": [album_entity(album, ctx) for album in albums]}}


async def h_get_random_songs(request, p, user):
    size = min(p.int("size", 10), 500)
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        tracks = repo.library_tracks(session)
    random.shuffle(tracks)
    return {"randomSongs": {"song": [song_entity(track, ctx) for track in tracks[:size]]}}


async def h_get_songs_by_genre(request, p, user):
    genre = (p.require("genre") or "").lower()
    size = min(p.int("count", 10), 500)
    offset = p.int("offset", 0)
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        tracks = [t for t in repo.library_tracks(session) if (t.get("genre") or "").lower() == genre]
    return {"songsByGenre": {"song": [song_entity(track, ctx) for track in tracks[offset : offset + size]]}}


async def h_get_starred(request, p, user):
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        songs = repo.starred_tracks(session)
        albums = repo.starred_albums(session)
        artists = repo.starred_artists(session)

    key = "starred2" if _action(request).endswith("2") else "starred"
    return {
        key: {
            "artist": [artist_entity(artist, ctx) for artist in artists],
            "album": [album_entity(album, ctx) for album in albums],
            "song": [song_entity(track, ctx) for track in songs],
        }
    }


async def h_get_now_playing(request, p, user):
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        entries = repo.now_playing(session)

    now = datetime.now(timezone.utc)
    songs = []
    for track, event in entries:
        entity = song_entity(track, ctx)
        played_at = event.played_at.replace(tzinfo=timezone.utc) if event.played_at.tzinfo is None else event.played_at
        entity["username"] = user
        entity["minutesAgo"] = int((now - played_at).total_seconds() // 60)
        entity["playerId"] = 1
        entity["playerName"] = event.client or "Music Play"
        songs.append(entity)

    return {"nowPlaying": {"entry": songs}}


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


async def h_search(request, p, user):
    is_v3 = _action(request).endswith("3")
    query = (p.get("query") or "").strip().strip('"')

    song_count = p.int("songCount", 20)
    album_count = p.int("albumCount", 20)
    artist_count = p.int("artistCount", 20)
    song_offset = p.int("songOffset", 0)
    album_offset = p.int("albumOffset", 0)
    artist_offset = p.int("artistOffset", 0)

    if not query:
        results = {"songs": [], "albums": [], "artists": []}
    else:
        results = await catalog.search(
            query,
            song_limit=song_count + song_offset,
            album_limit=album_count + album_offset,
            artist_limit=artist_count + artist_offset,
        )

    with session_scope() as session:
        ctx = LibraryContext.load(session)

    songs = results["songs"][song_offset : song_offset + song_count]
    albums = results["albums"][album_offset : album_offset + album_count]
    artists = results["artists"][artist_offset : artist_offset + artist_count]

    if is_v3:
        return {
            "searchResult3": {
                "artist": [artist_entity(artist, ctx) for artist in artists],
                "album": [album_entity(album, ctx) for album in albums],
                "song": [song_entity(track, ctx) for track in songs],
            }
        }

    # search2 predates the artist/album id model and expects directory children.
    return {
        "searchResult2": {
            "artist": [
                {"id": artist_entity(a, ctx)["id"], "name": a.get("name", "")} for a in artists
            ],
            "album": [directory_child_album(album, ctx) for album in albums],
            "song": [song_entity(track, ctx) for track in songs],
        }
    }


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------


async def h_get_playlists(request, p, user):
    entities = []
    with session_scope() as session:
        ctx = LibraryContext.load(session)
        for playlist in repo.list_playlists(session):
            entities.append(playlist_entity(repo.playlist_summary(session, playlist), user, ctx=ctx))

    for discovery in _discovery_playlists():
        entities.append(playlist_entity(discovery, "YouTube Music", ctx=ctx))

    for account in await catalog.account_playlists():
        entities.append(playlist_entity(account, account.get("owner", "YouTube Music"), ctx=ctx))

    return {"playlists": {"playlist": entities}}


async def h_get_playlist(request, p, user):
    loaded = await _load_playlist(p.require("id"))
    if loaded is None:
        raise SubsonicError(ERROR_NOT_FOUND, "Playlist nicht gefunden")
    summary, tracks = loaded
    with session_scope() as session:
        ctx = LibraryContext.load(session)
    return {"playlist": playlist_entity(summary, user, with_songs=tracks, ctx=ctx)}


def _video_ids(values: list[str]) -> list[str]:
    result = []
    for value in values:
        kind, payload = ids.parse(value)
        if kind == ids.SONG:
            result.append(payload)
    return result


async def h_create_playlist(request, p, user):
    playlist_id = p.get("playlistId")
    name = p.get("name")
    track_ids = _video_ids(p.all("songId"))

    # Ensure metadata exists for tracks the client only knows by id.
    await catalog.get_songs(track_ids)

    with session_scope() as session:
        if playlist_id:
            kind, payload = ids.parse(playlist_id)
            if kind != ids.PLAYLIST:
                raise SubsonicError(ERROR_NOT_FOUND, "Playlist nicht gefunden")
            playlist = repo.get_playlist(session, int(payload))
            if playlist is None:
                raise SubsonicError(ERROR_NOT_FOUND, "Playlist nicht gefunden")
            if name:
                playlist.name = name
            repo.replace_playlist_tracks(session, playlist.id, track_ids)
        else:
            if not name:
                raise SubsonicError(ERROR_MISSING_PARAM, "Erforderlicher Parameter 'name' fehlt")
            playlist = repo.create_playlist(session, name, track_ids)
        session.flush()
        summary = repo.playlist_summary(session, playlist)
        tracks = repo.playlist_tracks(session, playlist.id)
        ctx = LibraryContext.load(session)

    return {"playlist": playlist_entity(summary, user, with_songs=tracks, ctx=ctx)}


async def h_update_playlist(request, p, user):
    kind, payload = ids.parse(p.require("playlistId"))
    if kind != ids.PLAYLIST:
        raise SubsonicError(ERROR_NOT_FOUND, "Nur lokale Playlists können geändert werden")

    to_add = _video_ids(p.all("songIdToAdd"))
    await catalog.get_songs(to_add)

    remove_indexes = []
    for value in p.all("songIndexToRemove"):
        try:
            remove_indexes.append(int(value))
        except ValueError:
            continue

    with session_scope() as session:
        playlist = repo.get_playlist(session, int(payload))
        if playlist is None:
            raise SubsonicError(ERROR_NOT_FOUND, "Playlist nicht gefunden")
        if p.get("name"):
            playlist.name = p.get("name")
        if p.get("comment") is not None:
            playlist.comment = p.get("comment") or ""
        if p.get("public") is not None:
            playlist.public = p.bool("public")
        if remove_indexes:
            repo.remove_playlist_indexes(session, playlist.id, remove_indexes)
        if to_add:
            repo.append_playlist_tracks(session, playlist.id, to_add)

    return {}


async def h_delete_playlist(request, p, user):
    kind, payload = ids.parse(p.require("id"))
    if kind != ids.PLAYLIST:
        raise SubsonicError(ERROR_NOT_FOUND, "Nur lokale Playlists können gelöscht werden")
    with session_scope() as session:
        repo.delete_playlist(session, int(payload))
    return {}


# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------


def _annotation_targets(p: Params) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for value in p.all("id"):
        kind, _ = ids.parse(value)
        if kind == ids.SONG:
            targets.append((value, "song"))
        elif kind == ids.ALBUM:
            targets.append((value, "album"))
        elif kind == ids.ARTIST:
            targets.append((value, "artist"))
    targets.extend((value, "album") for value in p.all("albumId"))
    targets.extend((value, "artist") for value in p.all("artistId"))
    return targets


async def h_star(request, p, user):
    targets = _annotation_targets(p)

    # A starred item becomes part of the library, so make sure we hold metadata.
    for value, item_type in targets:
        kind, payload = ids.parse(value)
        if item_type == "song":
            await catalog.get_song(payload)
        elif item_type == "album":
            await catalog.get_album(payload)
        elif item_type == "artist" and kind == ids.ARTIST:
            channel_id, _ = ids.artist_payload(value)
            if channel_id:
                await catalog.get_artist(channel_id)

    with session_scope() as session:
        for value, item_type in targets:
            repo.star(session, value, item_type)
    return {}


async def h_unstar(request, p, user):
    with session_scope() as session:
        for value, item_type in _annotation_targets(p):
            repo.unstar(session, value, item_type)
    return {}


async def h_set_rating(request, p, user):
    value = p.require("id")
    rating = p.int("rating", 0)
    kind, _ = ids.parse(value)
    item_type = {ids.SONG: "song", ids.ALBUM: "album", ids.ARTIST: "artist"}.get(kind, "song")
    with session_scope() as session:
        repo.set_rating(session, value, item_type, rating)
    return {}


async def h_scrobble(request, p, user):
    if not p.bool("submission", True):
        return {}

    client_name = p.get("c") or "subsonic"
    for value in p.all("id"):
        kind, payload = ids.parse(value)
        if kind != ids.SONG:
            continue
        await catalog.get_song(payload)
        with session_scope() as session:
            repo.record_play(session, payload, client_name)
    return {}


# --------------------------------------------------------------------------
# Play queue
# --------------------------------------------------------------------------


async def h_save_play_queue(request, p, user):
    track_ids = _video_ids(p.all("id"))
    current = p.get("current")
    current_video = ids.parse(current)[1] if current else None
    with session_scope() as session:
        repo.save_play_queue(session, user, track_ids, current_video, float(p.int("position", 0)), p.get("c") or "")
    return {}


async def h_get_play_queue(request, p, user):
    with session_scope() as session:
        queue = repo.load_play_queue(session, user)
        if queue is None or not queue.track_ids:
            return {}
        ctx = LibraryContext.load(session)
        video_ids = [value for value in queue.track_ids.split(",") if value]
        lookup = repo.get_tracks(session, video_ids)
        entries = [song_entity(lookup[value], ctx) for value in video_ids if value in lookup]
        payload = {
            "current": ids.song_id(queue.current) if queue.current else None,
            "position": int(queue.position or 0),
            "username": user,
            "changed": iso(queue.changed_at),
            "changedBy": queue.changed_by or "Music Play",
            "entry": entries,
        }
    return {"playQueue": payload}


# --------------------------------------------------------------------------
# Lyrics
# --------------------------------------------------------------------------


async def h_get_lyrics(request, p, user):
    artist = p.get("artist") or ""
    title = p.get("title") or ""
    if not title:
        return {"lyrics": {"artist": artist, "title": title, "value": ""}}

    results = await catalog.search_scope(f"{artist} {title}".strip(), "songs", 1)
    if not results:
        return {"lyrics": {"artist": artist, "title": title, "value": ""}}

    found = await catalog.lyrics(results[0]["id"])
    return {"lyrics": {"artist": artist, "title": title, "value": (found or {}).get("text", "")}}


async def h_get_lyrics_by_song_id(request, p, user):
    kind, payload = ids.parse(p.require("id"))
    if kind != ids.SONG:
        raise SubsonicError(ERROR_NOT_FOUND, "Titel nicht gefunden")

    track = await catalog.get_song(payload)
    found = await catalog.lyrics(payload)
    if not found:
        return {"lyricsList": {}}

    structured = {
        "displayArtist": (track or {}).get("artist", ""),
        "displayTitle": (track or {}).get("title", ""),
        "lang": "xxx",
        "offset": 0,
        "synced": bool(found.get("synced")),
    }

    if found.get("synced"):
        structured["line"] = [
            {"start": line["start"], "value": line["text"]} for line in found["synced"] if line.get("text")
        ]
    else:
        structured["line"] = [{"value": line} for line in (found.get("text") or "").splitlines() if line.strip()]

    return {"lyricsList": {"structuredLyrics": [structured]}}


# --------------------------------------------------------------------------
# Empty-but-valid responses for features this server does not provide
# --------------------------------------------------------------------------


async def h_get_podcasts(request, p, user):
    """Subscribed YouTube Music podcasts, so Amperfy's podcast tab works."""
    include_episodes = p.bool("includeEpisodes", True)
    wanted = p.get("id")

    with session_scope() as session:
        ctx = LibraryContext.load(session)
        subscribed = repo.subscribed_podcasts(session)

    channels = []
    for show in subscribed:
        channel_id = ids.podcast_id(show["id"])
        if wanted and wanted != channel_id:
            continue
        episodes = None
        if include_episodes:
            full = await catalog.get_podcast(show["id"])
            episodes = (full or {}).get("episodes") or []
        channels.append(podcast_channel_entity(show, episodes, ctx))

    return {"podcasts": {"channel": channels}}


async def h_get_newest_podcasts(request, p, user):
    count = p.int("count", 20)
    episodes = await catalog.new_episodes()

    with session_scope() as session:
        ctx = LibraryContext.load(session)

    entities = [
        episode_entity(track, ids.podcast_id(track.get("podcast_id") or "unknown"), ctx)
        for track in episodes[:count]
    ]
    return {"newestPodcasts": {"episode": entities}}


async def h_empty_radio(request, p, user):
    return {"internetRadioStations": {}}


async def h_empty_bookmarks(request, p, user):
    return {"bookmarks": {}}


async def h_empty_shares(request, p, user):
    return {"shares": {}}


async def h_empty_chat(request, p, user):
    return {"chatMessages": {}}


async def h_empty_videos(request, p, user):
    return {"videos": {}}


async def h_ok(request, p, user):
    return {}


HANDLERS = {
    "ping": h_ping,
    "getlicense": h_get_license,
    "getopensubsonicextensions": h_get_open_subsonic_extensions,
    "getscanstatus": h_get_scan_status,
    "startscan": h_start_scan,
    "getuser": h_get_user,
    "getusers": h_get_users,
    "getmusicfolders": h_get_music_folders,
    "getindexes": h_get_indexes,
    "getartists": h_get_artists,
    "getartist": h_get_artist,
    "getalbum": h_get_album,
    "getsong": h_get_song,
    "getmusicdirectory": h_get_music_directory,
    "getgenres": h_get_genres,
    "getartistinfo": h_get_artist_info,
    "getartistinfo2": h_get_artist_info,
    "getalbuminfo": h_get_album_info,
    "getalbuminfo2": h_get_album_info,
    "getsimilarsongs": h_get_similar_songs,
    "getsimilarsongs2": h_get_similar_songs,
    "gettopsongs": h_get_top_songs,
    "getalbumlist": h_get_album_list,
    "getalbumlist2": h_get_album_list,
    "getrandomsongs": h_get_random_songs,
    "getsongsbygenre": h_get_songs_by_genre,
    "getstarred": h_get_starred,
    "getstarred2": h_get_starred,
    "getnowplaying": h_get_now_playing,
    "search2": h_search,
    "search3": h_search,
    "getplaylists": h_get_playlists,
    "getplaylist": h_get_playlist,
    "createplaylist": h_create_playlist,
    "updateplaylist": h_update_playlist,
    "deleteplaylist": h_delete_playlist,
    "star": h_star,
    "unstar": h_unstar,
    "setrating": h_set_rating,
    "scrobble": h_scrobble,
    "saveplayqueue": h_save_play_queue,
    "getplayqueue": h_get_play_queue,
    "getlyrics": h_get_lyrics,
    "getlyricsbysongid": h_get_lyrics_by_song_id,
    "getpodcasts": h_get_podcasts,
    "getnewestpodcasts": h_get_newest_podcasts,
    "getinternetradiostations": h_empty_radio,
    "getbookmarks": h_empty_bookmarks,
    "getshares": h_empty_shares,
    "getchatmessages": h_empty_chat,
    "getvideos": h_empty_videos,
    "addchatmessage": h_ok,
    "createbookmark": h_ok,
    "deletebookmark": h_ok,
}

# Binary responses bypass the XML/JSON envelope entirely.
BINARY_ACTIONS = {"stream", "download", "getcoverart", "hls"}


@router.api_route("/rest/{action}", methods=["GET", "POST"])
async def dispatch(request: Request, action: str) -> Response:
    name = action.removesuffix(".view").lower()
    request.state.subsonic_action = name

    try:
        params = await _collect_params(request)
        request.state.subsonic_params = params
        username = authenticate(request, params)

        if name in BINARY_ACTIONS:
            return await _dispatch_binary(request, params, name)

        handler = HANDLERS.get(name)
        if handler is None:
            raise SubsonicError(ERROR_GENERIC, f"Nicht unterstützte Operation: {action}")

        body = await handler(request, params, username)
        return render(request, body)

    except SubsonicError as error:
        return render_error(request, error.code, error.message)
    except Exception as exc:  # noqa: BLE001 - Subsonic clients need a valid envelope
        from app.core.logging import logger

        logger.exception("Fehler in /rest/{}: {}", action, exc)
        return render_error(request, ERROR_GENERIC, f"Interner Fehler: {exc}")


async def _dispatch_binary(request: Request, p: Params, name: str) -> Response:
    if name in ("stream", "download", "hls"):
        kind, payload = ids.parse(p.require("id"))
        if kind != ids.SONG:
            return Response(status_code=404, content=b"Titel nicht gefunden")
        return await media.stream_track(request, payload)

    if name == "getcoverart":
        return await _cover_art(p)

    return Response(status_code=404)


async def _cover_art(p: Params) -> Response:
    cover_id = p.require("id")
    size = p.int("size", 0) or None
    kind, payload = ids.parse(cover_id)

    url = ids.cover_payload(cover_id)

    if url is None and kind == ids.SONG:
        track = await catalog.get_song(payload)
        url = (track or {}).get("thumbnail") or f"https://i.ytimg.com/vi/{payload}/maxresdefault.jpg"
    elif url is None and kind == ids.ALBUM:
        with session_scope() as session:
            cached = repo.get_album(session, payload)
        url = (cached or {}).get("thumbnail") or ((await catalog.get_album(payload)) or {}).get("thumbnail")
    elif url is None and kind == ids.ARTIST:
        channel_id, _ = ids.artist_payload(cover_id)
        if channel_id:
            with session_scope() as session:
                cached = repo.get_artist(session, channel_id)
            url = (cached or {}).get("thumbnail") or ((await catalog.get_artist(channel_id)) or {}).get("thumbnail")
    elif url is None and kind == ids.PODCAST:
        with session_scope() as session:
            cached = repo.get_podcast(session, payload)
        url = (cached or {}).get("thumbnail") or ((await catalog.get_podcast(payload)) or {}).get("thumbnail")
    elif url is None and kind in (ids.PLAYLIST, ids.YT_PLAYLIST):
        loaded = await _load_playlist(cover_id)
        if loaded:
            summary, tracks = loaded
            url = summary.get("thumbnail") or (tracks[0]["thumbnail"] if tracks else None)

    if not url:
        return Response(status_code=404, content=b"Kein Cover")

    return await media.fetch_cover(url, size)


@router.get("/rest")
async def rest_root(request: Request) -> Response:
    return render(request, {"serverInfo": {"name": "Music Play", "version": APP_VERSION}})
