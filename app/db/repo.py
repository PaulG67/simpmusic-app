"""Library persistence.

The "library" this server exposes is deliberately curated rather than infinite:
favourites, playlists and play history. That keeps a full client-side sync
(Amperfy syncs everything up front) fast, while search stays live against
YouTube Music.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Album,
    Artist,
    LyricsTranslation,
    PlayEvent,
    Playlist,
    PlaylistEntry,
    PlayQueue,
    Podcast,
    Rating,
    Setting,
    Star,
    Track,
    utcnow,
)
from app.ytm import ids

TRACK_FIELDS = (
    "title",
    "artist",
    "artist_id",
    "album",
    "album_id",
    "duration",
    "thumbnail",
    "year",
    "track_no",
    "disc_no",
    "genre",
    "explicit",
)


def _track_to_dict(row: Track) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "artist": row.artist,
        "artist_id": row.artist_id,
        "album": row.album,
        "album_id": row.album_id,
        "duration": row.duration or 0,
        "thumbnail": row.thumbnail,
        "year": row.year,
        "track_no": row.track_no,
        "disc_no": row.disc_no,
        "genre": row.genre,
        "explicit": bool(row.explicit),
        "created_at": row.created_at,
    }


def _album_to_dict(row: Album) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "artist": row.artist,
        "artist_id": row.artist_id,
        "thumbnail": row.thumbnail,
        "year": row.year,
        "song_count": row.song_count or 0,
        "duration": row.duration or 0,
        "album_type": row.album_type,
        "created_at": row.created_at,
    }


def _artist_to_dict(row: Artist) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "thumbnail": row.thumbnail,
        "album_count": row.album_count or 0,
        "created_at": row.created_at,
    }


# --------------------------------------------------------------------------
# Metadata snapshots
# --------------------------------------------------------------------------


def upsert_track(session: Session, track: dict) -> None:
    track_id = track.get("id")
    if not track_id:
        return

    row = session.get(Track, track_id)
    if row is None:
        row = Track(id=track_id)
        session.add(row)

    for field in TRACK_FIELDS:
        value = track.get(field)
        # Never let a sparse result (e.g. a watch-playlist entry without album
        # info) erase richer data captured earlier.
        if value in (None, "", 0) and getattr(row, field, None):
            continue
        if value is not None:
            setattr(row, field, value)


def upsert_tracks(session: Session, tracks: list[dict]) -> None:
    for track in tracks:
        upsert_track(session, track)


def get_track(session: Session, track_id: str) -> dict | None:
    row = session.get(Track, track_id)
    return _track_to_dict(row) if row else None


def get_tracks(session: Session, track_ids: list[str]) -> dict[str, dict]:
    if not track_ids:
        return {}
    rows = session.scalars(select(Track).where(Track.id.in_(track_ids))).all()
    return {row.id: _track_to_dict(row) for row in rows}


def upsert_album(session: Session, album: dict) -> None:
    album_id = album.get("id")
    if not album_id:
        return

    row = session.get(Album, album_id)
    if row is None:
        row = Album(id=album_id)
        session.add(row)

    for field in ("name", "artist", "artist_id", "thumbnail", "year", "song_count", "duration", "album_type"):
        value = album.get(field)
        if value in (None, "", 0) and getattr(row, field, None):
            continue
        if value is not None:
            setattr(row, field, value)


def get_album(session: Session, album_id: str) -> dict | None:
    row = session.get(Album, album_id)
    return _album_to_dict(row) if row else None


def upsert_artist(session: Session, artist: dict) -> None:
    artist_id = artist.get("id")
    if not artist_id:
        return

    row = session.get(Artist, artist_id)
    if row is None:
        row = Artist(id=artist_id)
        session.add(row)

    for field in ("name", "thumbnail", "album_count"):
        value = artist.get(field)
        if value in (None, "", 0) and getattr(row, field, None):
            continue
        if value is not None:
            setattr(row, field, value)


def get_artist(session: Session, artist_id: str) -> dict | None:
    row = session.get(Artist, artist_id)
    return _artist_to_dict(row) if row else None


# --------------------------------------------------------------------------
# Favourites and ratings
# --------------------------------------------------------------------------


def star(session: Session, item_id: str, item_type: str) -> None:
    existing = session.scalar(
        select(Star).where(Star.item_id == item_id, Star.item_type == item_type)
    )
    if existing is None:
        session.add(Star(item_id=item_id, item_type=item_type))


def unstar(session: Session, item_id: str, item_type: str) -> None:
    session.execute(delete(Star).where(Star.item_id == item_id, Star.item_type == item_type))


def starred_map(session: Session, item_type: str | None = None) -> dict[str, datetime]:
    stmt = select(Star.item_id, Star.created_at)
    if item_type:
        stmt = stmt.where(Star.item_type == item_type)
    return {item_id: created for item_id, created in session.execute(stmt).all()}


def is_starred(session: Session, item_id: str, item_type: str) -> datetime | None:
    return session.scalar(
        select(Star.created_at).where(Star.item_id == item_id, Star.item_type == item_type)
    )


def _starred_entries(session: Session, item_type: str) -> list[tuple[str, datetime]]:
    """Stars are keyed by Subsonic id (`t_…`, `al_…`, `ar_…`) so that the web
    player and Amperfy share one set of favourites; the metadata tables are
    keyed by the bare YouTube id, hence the decode step."""
    return list(
        session.execute(
            select(Star.item_id, Star.created_at)
            .where(Star.item_type == item_type)
            .order_by(Star.created_at.desc())
        ).all()
    )


def starred_tracks(session: Session) -> list[dict]:
    entries = _starred_entries(session, "song")
    payloads = [ids.parse(item_id)[1] for item_id, _ in entries]
    lookup = get_tracks(session, payloads)

    result = []
    for (item_id, created), video_id in zip(entries, payloads):
        track = lookup.get(video_id)
        if track:
            result.append({**track, "starred_at": created})
    return result


def starred_albums(session: Session) -> list[dict]:
    result = []
    for item_id, created in _starred_entries(session, "album"):
        row = session.get(Album, ids.parse(item_id)[1])
        if row is not None:
            result.append({**_album_to_dict(row), "starred_at": created})
    return result


def starred_artists(session: Session) -> list[dict]:
    result = []
    for item_id, created in _starred_entries(session, "artist"):
        channel_id, fallback_name = ids.artist_payload(item_id)
        row = session.get(Artist, channel_id) if channel_id else None
        if row is not None:
            result.append({**_artist_to_dict(row), "starred_at": created})
        elif fallback_name:
            # Artist that only exists as a name on library tracks.
            result.append(
                {
                    "id": None,
                    "name": fallback_name,
                    "thumbnail": None,
                    "album_count": 0,
                    "created_at": created,
                    "starred_at": created,
                }
            )
    return result


def set_rating(session: Session, item_id: str, item_type: str, rating: int) -> None:
    row = session.scalar(
        select(Rating).where(Rating.item_id == item_id, Rating.item_type == item_type)
    )
    if rating <= 0:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        session.add(Rating(item_id=item_id, item_type=item_type, rating=rating))
    else:
        row.rating = rating


def rating_map(session: Session) -> dict[str, int]:
    return {item_id: value for item_id, value in session.execute(select(Rating.item_id, Rating.rating)).all()}


# --------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------


def list_playlists(session: Session) -> list[Playlist]:
    return list(session.scalars(select(Playlist).order_by(Playlist.name)).all())


def get_playlist(session: Session, playlist_id: int) -> Playlist | None:
    return session.get(Playlist, playlist_id)


def playlist_track_ids(session: Session, playlist_id: int) -> list[str]:
    return list(
        session.scalars(
            select(PlaylistEntry.track_id)
            .where(PlaylistEntry.playlist_id == playlist_id)
            .order_by(PlaylistEntry.position)
        ).all()
    )


def playlist_tracks(session: Session, playlist_id: int) -> list[dict]:
    ids = playlist_track_ids(session, playlist_id)
    lookup = get_tracks(session, ids)
    return [lookup[track_id] for track_id in ids if track_id in lookup]


def create_playlist(session: Session, name: str, track_ids: list[str] | None = None, ytm_id: str | None = None) -> Playlist:
    playlist = Playlist(name=name or "Neue Playlist", ytm_id=ytm_id)
    session.add(playlist)
    session.flush()
    for position, track_id in enumerate(track_ids or []):
        session.add(PlaylistEntry(playlist_id=playlist.id, track_id=track_id, position=position))
    return playlist


def replace_playlist_tracks(session: Session, playlist_id: int, track_ids: list[str]) -> None:
    session.execute(delete(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist_id))
    for position, track_id in enumerate(track_ids):
        session.add(PlaylistEntry(playlist_id=playlist_id, track_id=track_id, position=position))


def append_playlist_tracks(session: Session, playlist_id: int, track_ids: list[str]) -> None:
    start = session.scalar(
        select(func.count()).select_from(PlaylistEntry).where(PlaylistEntry.playlist_id == playlist_id)
    ) or 0
    for offset, track_id in enumerate(track_ids):
        session.add(PlaylistEntry(playlist_id=playlist_id, track_id=track_id, position=start + offset))


def remove_playlist_indexes(session: Session, playlist_id: int, indexes: list[int]) -> None:
    ids = playlist_track_ids(session, playlist_id)
    keep = [track_id for position, track_id in enumerate(ids) if position not in set(indexes)]
    replace_playlist_tracks(session, playlist_id, keep)


def delete_playlist(session: Session, playlist_id: int) -> None:
    playlist = session.get(Playlist, playlist_id)
    if playlist is not None:
        session.delete(playlist)


def playlist_summary(session: Session, playlist: Playlist) -> dict:
    ids = playlist_track_ids(session, playlist.id)
    lookup = get_tracks(session, ids)
    duration = sum(lookup[track_id]["duration"] for track_id in ids if track_id in lookup)
    cover = next((lookup[t]["thumbnail"] for t in ids if lookup.get(t, {}).get("thumbnail")), None)
    return {
        "id": playlist.id,
        "name": playlist.name,
        "comment": playlist.comment or "",
        "public": bool(playlist.public),
        "song_count": len(ids),
        "duration": duration,
        "created_at": playlist.created_at,
        "updated_at": playlist.updated_at,
        "thumbnail": cover,
        "ytm_id": playlist.ytm_id,
    }


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def record_play(session: Session, track_id: str, client: str = "", seconds: int = 0) -> None:
    session.add(PlayEvent(track_id=track_id, client=client[:64], seconds=max(int(seconds or 0), 0)))


def play_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(PlayEvent.track_id, func.count(PlayEvent.id)).group_by(PlayEvent.track_id)
    ).all()
    return {track_id: count for track_id, count in rows}


def recently_played_tracks(session: Session, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(PlayEvent.track_id, func.max(PlayEvent.played_at).label("last"))
        .group_by(PlayEvent.track_id)
        .order_by(func.max(PlayEvent.played_at).desc())
        .limit(limit)
    ).all()
    ids = [row[0] for row in rows]
    lookup = get_tracks(session, ids)
    return [lookup[track_id] for track_id in ids if track_id in lookup]


def most_played_tracks(session: Session, limit: int = 50) -> list[dict]:
    rows = session.execute(
        select(PlayEvent.track_id, func.count(PlayEvent.id).label("plays"))
        .group_by(PlayEvent.track_id)
        .order_by(func.count(PlayEvent.id).desc())
        .limit(limit)
    ).all()
    ids = [row[0] for row in rows]
    lookup = get_tracks(session, ids)
    return [lookup[track_id] for track_id in ids if track_id in lookup]


def now_playing(session: Session, within_minutes: int = 10) -> list[tuple[dict, PlayEvent]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=within_minutes)
    events = session.scalars(
        select(PlayEvent).where(PlayEvent.played_at >= cutoff).order_by(PlayEvent.played_at.desc())
    ).all()
    seen: set[str] = set()
    result = []
    for event in events:
        if event.track_id in seen:
            continue
        seen.add(event.track_id)
        track = get_track(session, event.track_id)
        if track:
            result.append((track, event))
    return result


# --------------------------------------------------------------------------
# Library views
# --------------------------------------------------------------------------


def library_track_ids(session: Session) -> list[str]:
    """Everything a client should see as "in the library"."""
    starred = {
        ids.parse(item_id)[1]
        for item_id in session.scalars(select(Star.item_id).where(Star.item_type == "song")).all()
    }
    in_playlists = set(session.scalars(select(PlaylistEntry.track_id)).all())
    return sorted(starred | in_playlists)


def library_tracks(session: Session) -> list[dict]:
    ids = library_track_ids(session)
    lookup = get_tracks(session, ids)
    return [lookup[track_id] for track_id in ids if track_id in lookup]


def library_artists(session: Session) -> list[dict]:
    """Artists derived from library tracks, merged with explicitly starred ones."""
    artists: dict[str, dict] = {}

    for artist in starred_artists(session):
        key = artist["id"] or f"name:{artist['name'].lower()}"
        artists[key] = {**artist, "song_count": 0}

    for track in library_tracks(session):
        artist_id = track.get("artist_id")
        name = track.get("artist") or "Unbekannt"
        key = artist_id or f"name:{name.lower()}"
        entry = artists.get(key)
        if entry is None:
            cached = get_artist(session, artist_id) if artist_id else None
            entry = {
                "id": artist_id or key,
                "name": name,
                "thumbnail": (cached or {}).get("thumbnail") or track.get("thumbnail"),
                "album_count": 0,
                "song_count": 0,
            }
            artists[key] = entry
        entry["song_count"] = entry.get("song_count", 0) + 1

    for artist in artists.values():
        artist.setdefault("song_count", 0)
    return sorted(artists.values(), key=lambda item: item["name"].lower())


def library_albums(session: Session) -> list[dict]:
    """Starred albums plus albums implied by library tracks."""
    albums: dict[str, dict] = {}

    for album in starred_albums(session):
        albums[album["id"]] = album

    for track in library_tracks(session):
        album_id = track.get("album_id")
        if not album_id or album_id in albums:
            continue
        cached = get_album(session, album_id)
        albums[album_id] = cached or {
            "id": album_id,
            "name": track.get("album") or "Unbekanntes Album",
            "artist": track.get("artist") or "",
            "artist_id": track.get("artist_id"),
            "thumbnail": track.get("thumbnail"),
            "year": track.get("year"),
            "song_count": 0,
            "duration": 0,
            "created_at": track.get("created_at"),
        }

    return sorted(albums.values(), key=lambda item: (item.get("name") or "").lower())


def library_genres(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Track.genre, func.count(Track.id))
        .where(Track.genre.is_not(None), Track.genre != "")
        .group_by(Track.genre)
        .order_by(func.count(Track.id).desc())
    ).all()
    return [(genre, count) for genre, count in rows]


# --------------------------------------------------------------------------
# Play queue (Subsonic savePlayQueue/getPlayQueue)
# --------------------------------------------------------------------------


def save_play_queue(session: Session, username: str, track_ids: list[str], current: str | None, position_ms: float, client: str) -> None:
    row = session.scalar(select(PlayQueue).where(PlayQueue.username == username))
    if row is None:
        row = PlayQueue(username=username)
        session.add(row)
    row.track_ids = ",".join(track_ids)
    row.current = current
    row.position = position_ms
    row.changed_by = client[:64]
    row.changed_at = utcnow()


def load_play_queue(session: Session, username: str) -> PlayQueue | None:
    return session.scalar(select(PlayQueue).where(PlayQueue.username == username))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return default


def set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        session.add(row)
    row.value = json.dumps(value, ensure_ascii=False)


def all_settings(session: Session) -> dict[str, Any]:
    result = {}
    for row in session.scalars(select(Setting)).all():
        try:
            result[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            continue
    return result


# --------------------------------------------------------------------------
# Podcasts
# --------------------------------------------------------------------------


def upsert_podcast(session: Session, podcast: dict) -> None:
    podcast_id = podcast.get("id")
    if not podcast_id:
        return

    row = session.get(Podcast, podcast_id)
    if row is None:
        row = Podcast(id=podcast_id)
        session.add(row)

    for field in ("title", "author", "description", "thumbnail"):
        value = podcast.get(field)
        if value:
            setattr(row, field, value)


def get_podcast(session: Session, podcast_id: str) -> dict | None:
    row = session.get(Podcast, podcast_id)
    if row is None:
        return None
    return {
        "id": row.id,
        "title": row.title,
        "author": row.author,
        "description": row.description,
        "thumbnail": row.thumbnail,
        "created_at": row.created_at,
    }


def subscribed_podcasts(session: Session) -> list[dict]:
    result = []
    for item_id, created in _starred_entries(session, "podcast"):
        found = get_podcast(session, ids.parse(item_id)[1])
        if found:
            result.append({**found, "starred_at": created})
    return result


# --------------------------------------------------------------------------
# Lyrics translation cache
# --------------------------------------------------------------------------


def get_translation(session: Session, video_id: str, language: str) -> str | None:
    row = session.get(LyricsTranslation, f"{video_id}:{language}")
    return row.text if row else None


def save_translation(session: Session, video_id: str, language: str, text: str) -> None:
    key = f"{video_id}:{language}"
    row = session.get(LyricsTranslation, key)
    if row is None:
        row = LyricsTranslation(id=key, video_id=video_id, language=language)
        session.add(row)
    row.text = text
