"""Translate internal dicts into Subsonic/OpenSubsonic entities."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import repo
from app.subsonic.response import iso
from app.ytm import ids

DEFAULT_BITRATE = 128
DEFAULT_SUFFIX = "m4a"
DEFAULT_CONTENT_TYPE = "audio/mp4"

_UNSAFE_PATH = re.compile(r"[\\/:*?\"<>|]+")


@dataclass
class LibraryContext:
    """Per-request lookup tables so entity rendering stays free of N+1 queries."""

    starred: dict[str, datetime] = field(default_factory=dict)
    ratings: dict[str, int] = field(default_factory=dict)
    plays: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> "LibraryContext":
        return cls(
            starred=repo.starred_map(session),
            ratings=repo.rating_map(session),
            plays=repo.play_counts(session),
        )


EMPTY_CONTEXT = LibraryContext()


def _safe(part: str) -> str:
    return _UNSAFE_PATH.sub("_", (part or "").strip()) or "Unbekannt"


def estimated_size(duration: int, bitrate: int = DEFAULT_BITRATE) -> int:
    return int(max(duration, 0) * bitrate * 1000 / 8)


def song_entity(track: dict, ctx: LibraryContext = EMPTY_CONTEXT) -> dict:
    video_id = track["id"]
    song_id = ids.song_id(video_id)
    album_id = ids.album_id(track["album_id"]) if track.get("album_id") else None
    artist_id = ids.artist_id(track.get("artist_id"), track.get("artist", "")) if track.get("artist") or track.get("artist_id") else None

    duration = int(track.get("duration") or 0)
    bitrate = int(track.get("bitrate") or DEFAULT_BITRATE)
    track_no = track.get("track_no")

    filename = f"{int(track_no):02d} - {_safe(track.get('title'))}" if track_no else _safe(track.get("title"))

    entity = {
        "id": song_id,
        "parent": album_id or artist_id,
        "isDir": False,
        "title": track.get("title") or "Unbekannter Titel",
        "album": track.get("album") or "",
        "artist": track.get("artist") or "",
        "track": int(track_no) if track_no else None,
        "year": track.get("year"),
        "genre": track.get("genre"),
        "coverArt": song_id,
        "size": track.get("size") or estimated_size(duration, bitrate),
        "contentType": track.get("content_type") or DEFAULT_CONTENT_TYPE,
        "suffix": track.get("suffix") or DEFAULT_SUFFIX,
        "duration": duration,
        "bitRate": bitrate,
        "path": f"{_safe(track.get('artist'))}/{_safe(track.get('album') or 'Singles')}/{filename}.{DEFAULT_SUFFIX}",
        "playCount": ctx.plays.get(video_id, 0) or None,
        "discNumber": track.get("disc_no") or 1,
        "created": iso(track.get("created_at")),
        "albumId": album_id,
        "artistId": artist_id,
        "type": "music",
        "isVideo": False,
        "starred": iso(ctx.starred.get(song_id)),
        "userRating": ctx.ratings.get(song_id),
        # OpenSubsonic additions
        "mediaType": "song",
        "displayArtist": track.get("artist") or "",
        "sortName": track.get("title") or "",
        "channelCount": 2,
        "samplingRate": 44100,
        "explicitStatus": "explicit" if track.get("explicit") else None,
    }

    if artist_id:
        entity["artists"] = [{"id": artist_id, "name": track.get("artist") or ""}]
        entity["albumArtists"] = [{"id": artist_id, "name": track.get("artist") or ""}]
    if track.get("genre"):
        entity["genres"] = [{"name": track["genre"]}]

    return entity


def album_entity(album: dict, ctx: LibraryContext = EMPTY_CONTEXT, with_songs: bool = False) -> dict:
    album_id = ids.album_id(album["id"])
    artist_id = ids.artist_id(album.get("artist_id"), album.get("artist", "")) if album.get("artist") or album.get("artist_id") else None
    tracks = album.get("tracks") or []

    entity = {
        "id": album_id,
        "parent": artist_id,
        "isDir": True,
        "name": album.get("name") or "Unbekanntes Album",
        "title": album.get("name") or "Unbekanntes Album",
        "album": album.get("name") or "",
        "artist": album.get("artist") or "",
        "artistId": artist_id,
        "coverArt": album_id,
        "songCount": int(album.get("song_count") or len(tracks) or 0),
        "duration": int(album.get("duration") or sum(t.get("duration", 0) for t in tracks)),
        "playCount": sum(ctx.plays.get(t["id"], 0) for t in tracks) or None,
        "created": iso(album.get("created_at")),
        "year": album.get("year"),
        "genre": album.get("genre"),
        "starred": iso(ctx.starred.get(album_id)),
        "userRating": ctx.ratings.get(album_id),
        # OpenSubsonic additions
        "displayArtist": album.get("artist") or "",
        "sortName": album.get("name") or "",
    }

    if artist_id:
        entity["artists"] = [{"id": artist_id, "name": album.get("artist") or ""}]

    if with_songs:
        entity["song"] = [song_entity(track, ctx) for track in tracks]

    return entity


def artist_entity(artist: dict, ctx: LibraryContext = EMPTY_CONTEXT, with_albums: bool = False) -> dict:
    artist_id = ids.artist_id(artist.get("id"), artist.get("name", ""))
    albums = artist.get("albums") or []

    entity = {
        "id": artist_id,
        "name": artist.get("name") or "Unbekannter Interpret",
        "coverArt": artist_id,
        "artistImageUrl": artist.get("thumbnail"),
        "albumCount": int(artist.get("album_count") or len(albums) or 0),
        "starred": iso(ctx.starred.get(artist_id)),
        "userRating": ctx.ratings.get(artist_id),
        "sortName": artist.get("name") or "",
    }

    if with_albums:
        entity["album"] = [album_entity(album, ctx) for album in albums]

    return entity


def directory_child_album(album: dict, ctx: LibraryContext = EMPTY_CONTEXT) -> dict:
    entity = album_entity(album, ctx)
    entity["isDir"] = True
    entity["title"] = entity["name"]
    return entity


def episode_entity(track: dict, channel_id: str, ctx: LibraryContext = EMPTY_CONTEXT) -> dict:
    """A podcast episode reuses the song shape plus the podcast-specific fields
    Subsonic clients look for."""
    entity = song_entity(track, ctx)
    entity.update(
        {
            "streamId": entity["id"],
            "channelId": channel_id,
            "parent": channel_id,
            "description": track.get("description") or "",
            "publishDate": track.get("published") or None,
            "status": "completed",
            "mediaType": "podcast",
            "type": "podcast",
        }
    )
    return entity


def podcast_channel_entity(
    podcast: dict,
    episodes: list[dict] | None = None,
    ctx: LibraryContext = EMPTY_CONTEXT,
) -> dict:
    channel_id = ids.podcast_id(podcast["id"])
    entity = {
        "id": channel_id,
        "url": f"https://music.youtube.com/playlist?list={podcast['id']}",
        "title": podcast.get("title") or "Podcast",
        "description": podcast.get("description") or "",
        "coverArt": channel_id,
        "originalImageUrl": podcast.get("thumbnail"),
        "status": "completed",
    }
    if episodes is not None:
        entity["episode"] = [episode_entity(track, channel_id, ctx) for track in episodes]
    return entity


def playlist_entity(summary: dict, owner: str, with_songs: list[dict] | None = None, ctx: LibraryContext = EMPTY_CONTEXT) -> dict:
    playlist_id = ids.playlist_id(summary["id"]) if isinstance(summary["id"], int) else ids.yt_playlist_id(summary["id"])

    entity = {
        "id": playlist_id,
        "name": summary.get("name") or "Playlist",
        "comment": summary.get("comment") or summary.get("description") or "",
        "owner": owner,
        "public": bool(summary.get("public")),
        "songCount": int(summary.get("song_count") or 0),
        "duration": int(summary.get("duration") or 0),
        "created": iso(summary.get("created_at")),
        "changed": iso(summary.get("updated_at") or summary.get("created_at")),
        "coverArt": playlist_id,
    }

    if with_songs is not None:
        entity["entry"] = [song_entity(track, ctx) for track in with_songs]

    return entity
