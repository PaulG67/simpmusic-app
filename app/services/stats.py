"""Listening statistics and the yearly recap.

Everything is derived from the local `play_events` table - no external service
ever sees what you listen to.
"""

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repo
from app.db.models import PlayEvent

WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def _events(session: Session, year: int | None = None) -> list[PlayEvent]:
    stmt = select(PlayEvent)
    if year is not None:
        stmt = stmt.where(
            PlayEvent.played_at >= datetime(year, 1, 1),
            PlayEvent.played_at < datetime(year + 1, 1, 1),
        )
    return list(session.scalars(stmt.order_by(PlayEvent.played_at)).all())


def _seconds(event: PlayEvent, track: dict | None) -> int:
    """Older events predate duration tracking; fall back to the track length."""
    if event.seconds:
        return event.seconds
    return int((track or {}).get("duration") or 0)


def _resolve(session: Session, events: list[PlayEvent]) -> dict[str, dict]:
    return repo.get_tracks(session, list({event.track_id for event in events}))


def _ranked(counter: Counter, seconds: Counter, limit: int) -> list[dict]:
    return [
        {"name": name, "plays": plays, "seconds": seconds.get(name, 0)}
        for name, plays in counter.most_common(limit)
    ]


def overview(session: Session, limit: int = 10) -> dict:
    events = _events(session)
    tracks = _resolve(session, events)

    if not events:
        return {
            "totalPlays": 0,
            "totalSeconds": 0,
            "uniqueTracks": 0,
            "uniqueArtists": 0,
            "topArtists": [],
            "topTracks": [],
            "topAlbums": [],
            "byWeekday": [{"label": day, "plays": 0} for day in WEEKDAYS],
            "byHour": [{"label": f"{hour:02d}", "plays": 0} for hour in range(24)],
            "byMonth": [],
            "years": [],
        }

    artist_plays, artist_seconds = Counter(), Counter()
    track_plays, track_seconds = Counter(), Counter()
    album_plays, album_seconds = Counter(), Counter()
    weekday, hour, month = Counter(), Counter(), Counter()
    track_meta: dict[str, dict] = {}
    total_seconds = 0

    for event in events:
        track = tracks.get(event.track_id)
        seconds = _seconds(event, track)
        total_seconds += seconds

        weekday[event.played_at.weekday()] += 1
        hour[event.played_at.hour] += 1
        month[(event.played_at.year, event.played_at.month)] += 1

        if not track:
            continue

        artist = track.get("artist") or "Unbekannt"
        artist_plays[artist] += 1
        artist_seconds[artist] += seconds

        label = f"{track.get('title')} – {artist}"
        track_plays[label] += 1
        track_seconds[label] += seconds
        track_meta[label] = track

        if track.get("album"):
            album_label = f"{track['album']} – {artist}"
            album_plays[album_label] += 1
            album_seconds[album_label] += seconds

    top_tracks = _ranked(track_plays, track_seconds, limit)
    for entry in top_tracks:
        meta = track_meta.get(entry["name"], {})
        entry["thumbnail"] = meta.get("thumbnail")
        entry["videoId"] = meta.get("id")

    return {
        "totalPlays": len(events),
        "totalSeconds": total_seconds,
        "uniqueTracks": len({event.track_id for event in events}),
        "uniqueArtists": len(artist_plays),
        "topArtists": _ranked(artist_plays, artist_seconds, limit),
        "topTracks": top_tracks,
        "topAlbums": _ranked(album_plays, album_seconds, limit),
        "byWeekday": [{"label": day, "plays": weekday.get(index, 0)} for index, day in enumerate(WEEKDAYS)],
        "byHour": [{"label": f"{value:02d}", "plays": hour.get(value, 0)} for value in range(24)],
        "byMonth": [
            {"label": f"{MONTHS[key[1] - 1]} {key[0]}", "plays": value}
            for key, value in sorted(month.items())
        ],
        "years": sorted({event.played_at.year for event in events}, reverse=True),
    }


def _longest_streak(days: set) -> int:
    if not days:
        return 0
    ordered = sorted(days)
    best = run = 1
    for previous, current in zip(ordered, ordered[1:]):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        best = max(best, run)
    return best


def wrapped(session: Session, year: int) -> dict:
    events = _events(session, year)
    tracks = _resolve(session, events)

    if not events:
        return {"year": year, "empty": True}

    artist_plays, artist_seconds = Counter(), Counter()
    track_plays = Counter()
    track_meta: dict[str, dict] = {}
    album_plays = Counter()
    days = set()
    total_seconds = 0
    busiest = Counter()

    for event in events:
        track = tracks.get(event.track_id)
        seconds = _seconds(event, track)
        total_seconds += seconds
        days.add(event.played_at.date())
        busiest[event.played_at.date()] += 1

        if not track:
            continue

        artist = track.get("artist") or "Unbekannt"
        artist_plays[artist] += 1
        artist_seconds[artist] += seconds
        track_plays[event.track_id] += 1
        track_meta[event.track_id] = track
        if track.get("album"):
            album_plays[f"{track['album']} – {artist}"] += 1

    top_tracks = []
    for track_id, plays in track_plays.most_common(5):
        meta = track_meta.get(track_id, {})
        top_tracks.append(
            {
                "name": f"{meta.get('title')} – {meta.get('artist')}",
                "plays": plays,
                "thumbnail": meta.get("thumbnail"),
                "videoId": track_id,
            }
        )

    busiest_day, busiest_plays = busiest.most_common(1)[0]
    top_artist = artist_plays.most_common(1)[0][0] if artist_plays else None

    return {
        "year": year,
        "empty": False,
        "totalPlays": len(events),
        "totalMinutes": round(total_seconds / 60),
        "totalHours": round(total_seconds / 3600, 1),
        "uniqueTracks": len(track_plays),
        "uniqueArtists": len(artist_plays),
        "activeDays": len(days),
        "longestStreak": _longest_streak(days),
        "busiestDay": {"date": busiest_day.isoformat(), "plays": busiest_plays},
        "topArtist": top_artist,
        "topArtists": _ranked(artist_plays, artist_seconds, 5),
        "topTracks": top_tracks,
        "topAlbums": [{"name": name, "plays": plays} for name, plays in album_plays.most_common(5)],
        "firstPlay": events[0].played_at.isoformat(),
        "lastPlay": events[-1].played_at.isoformat(),
    }
