"""Match YouTube Music tracks against the Navidrome library."""

from __future__ import annotations

import re
import threading
import time
import unicodedata

from app.core.logging import logger
from app.db import repo
from app.db.database import session_scope
from app.navidrome import config
from app.navidrome.client import NavidromeClient, map_song

_NOISE = re.compile(
    r"\b(official(\s+(music|lyric))?(\s+video)?|lyrics?|audio|visuali[sz]er|"
    r"hd|4k|remaster(ed)?|video|topic|performance)\b",
    re.IGNORECASE,
)
_FEAT = re.compile(r"\b(feat|ft|featuring|with)\b.*$", re.IGNORECASE)

_index_lock = threading.Lock()
_index: list[dict] = []
_index_by_key: dict[str, list[dict]] = {}
_index_at = 0.0
INDEX_TTL = 300


def fold(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"[\(\[].*?[\)\]]", " ", text)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def artist_tokens(value: str | None) -> set[str]:
    text = _FEAT.sub("", fold(value))
    return {part for part in text.split() if len(part) > 1}


def titles_match(left: str, right: str) -> bool:
    a, b = fold(left), fold(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def artists_match(left: str, right: str) -> bool:
    tokens_a, tokens_b = artist_tokens(left), artist_tokens(right)
    if tokens_a and tokens_b:
        return bool(tokens_a & tokens_b)
    return fold(left) == fold(right)


def duration_close(left: int | None, right: int | None) -> bool:
    if not left or not right:
        return True
    return abs(int(left) - int(right)) <= 12


def score(track: dict, candidate: dict) -> int:
    if not titles_match(track.get("title") or "", candidate.get("title") or ""):
        return 0
    if not artists_match(track.get("artist") or "", candidate.get("artist") or ""):
        return 0
    if not duration_close(track.get("duration"), candidate.get("duration")):
        return 0
    points = 10
    if fold(track.get("title") or "") == fold(candidate.get("title") or ""):
        points += 6
    if fold(track.get("album") or "") and fold(track.get("album") or "") == fold(candidate.get("album") or ""):
        points += 3
    if duration_close(track.get("duration"), candidate.get("duration")) and track.get("duration"):
        points += 2
    return points


def _key(title: str, artist: str) -> str:
    return f"{fold(title)}|{fold(artist)}"


def _index_stale() -> bool:
    return (time.monotonic() - _index_at) > INDEX_TTL or not _index


def refresh_index(force: bool = False) -> int:
    global _index, _index_by_key, _index_at
    if not config.configured():
        return 0
    with _index_lock:
        if not force and not _index_stale():
            return len(_index)
        try:
            with NavidromeClient() as client:
                songs = client.list_songs()
        except Exception as exc:
            logger.warning("Navidrome-Index konnte nicht geladen werden: {}", exc)
            return len(_index)

        by_key: dict[str, list[dict]] = {}
        for song in songs:
            key = _key(song.get("title") or "", song.get("artist") or "")
            by_key.setdefault(key, []).append(song)
            folded_title = fold(song.get("title") or "")
            if folded_title:
                by_key.setdefault(folded_title, []).append(song)
        _index = songs
        _index_by_key = by_key
        _index_at = time.monotonic()
        logger.info("Navidrome-Index: {} Titel", len(songs))
        return len(songs)


def index_size() -> int:
    with _index_lock:
        return len(_index)


def indexed_songs() -> list[dict]:
    if _index_stale():
        refresh_index()
    with _index_lock:
        return list(_index)


def _candidates_from_index(track: dict) -> list[dict]:
    title = track.get("title") or ""
    artist = track.get("artist") or ""
    with _index_lock:
        found = list(_index_by_key.get(_key(title, artist)) or [])
        if not found:
            found = list(_index_by_key.get(fold(title)) or [])
        snapshot = list(_index) if not found else []
    if found:
        return found
    # Last resort: scan a slice of the index for fuzzy title matches.
    matches = []
    for song in snapshot[:8000]:
        if titles_match(title, song.get("title") or ""):
            matches.append(song)
            if len(matches) >= 12:
                break
    return matches


def pick(track: dict, candidates: list[dict]) -> dict | None:
    ranked = []
    for candidate in candidates:
        points = score(track, candidate)
        if points:
            ranked.append((points, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def find_in_index(track: dict) -> dict | None:
    if not (track.get("title") and track.get("artist")):
        return None
    if not _index:
        return None
    return pick(track, _candidates_from_index(track))


def find_live(track: dict) -> dict | None:
    query = " ".join(part for part in (track.get("artist"), track.get("title")) if part).strip()
    if not query or not config.configured():
        return None
    try:
        with NavidromeClient() as client:
            result = client.search(query, songs=20, albums=0, artists=0)
    except Exception as exc:
        logger.debug("Navidrome-Suche für '{}' fehlgeschlagen: {}", query, exc)
        return None
    raw_candidates = []
    for song in result.get("songs") or []:
        raw_candidates.append(
            {
                "id": song.get("navidromeId"),
                "title": song.get("title"),
                "artist": song.get("artist"),
                "album": song.get("album"),
                "duration": song.get("duration"),
                "path": song.get("path"),
                "coverArt": (song.get("thumbnail") or "").rsplit("/", 1)[-1],
                "albumId": nd_unwrap(song.get("album_id")),
                "artistId": nd_unwrap(song.get("artist_id")),
            }
        )
    return pick(track, raw_candidates)


def nd_unwrap(value: str | None) -> str | None:
    from app.navidrome import ids as nd_ids

    return nd_ids.unwrap(value) if value else None


def resolve(track: dict) -> dict | None:
    """Return a Navidrome song dict (id/title/artist/...) or None."""
    video_id = track.get("id") or ""
    if video_id.startswith("nd:"):
        return {"id": nd_unwrap(video_id)}

    with session_scope() as session:
        cached = repo.get_navidrome_link(session, video_id)
    if cached:
        return {
            "id": cached["navidrome_id"],
            "title": cached.get("title") or track.get("title"),
            "artist": cached.get("artist") or track.get("artist"),
            "path": cached.get("path") or "",
        }

    found = find_in_index(track) or find_live(track)
    if not found or not found.get("id"):
        return None

    with session_scope() as session:
        repo.upsert_navidrome_link(
            session,
            video_id,
            str(found["id"]),
            title=found.get("title") or track.get("title") or "",
            artist=found.get("artist") or track.get("artist") or "",
            path=found.get("path") or "",
        )
    return found


def enrich(tracks: list[dict], live_limit: int = 0) -> list[dict]:
    """Attach navidromeId / inLibrary to YouTube (or mixed) track dicts."""
    if not tracks:
        return tracks
    if not config.configured():
        return [{**track, "inLibrary": bool(track.get("inLibrary")), "navidromeId": track.get("navidromeId")} for track in tracks]

    video_ids = [track.get("id") for track in tracks if track.get("id") and not str(track.get("id")).startswith("nd:")]
    with session_scope() as session:
        links = repo.get_navidrome_links(session, video_ids)

    if _index_stale():
        # Don't block the request on a full refresh; use whatever is cached.
        pass

    live_used = 0
    decorated = []
    for track in tracks:
        item = dict(track)
        video_id = item.get("id") or ""
        if item.get("navidromeId") or str(video_id).startswith("nd:"):
            item["inLibrary"] = True
            item["navidromeId"] = item.get("navidromeId") or nd_unwrap(video_id)
            item["source"] = item.get("source") or ("navidrome" if str(video_id).startswith("nd:") else "both")
            decorated.append(item)
            continue

        link = links.get(video_id)
        found = None
        if link:
            found = {"id": link["navidrome_id"], "path": link.get("path") or ""}
        elif _index:
            found = find_in_index(item)
            if found is None and live_used < live_limit:
                found = find_live(item)
                live_used += 1
            if found and found.get("id"):
                with session_scope() as session:
                    repo.upsert_navidrome_link(
                        session,
                        video_id,
                        str(found["id"]),
                        title=found.get("title") or item.get("title") or "",
                        artist=found.get("artist") or item.get("artist") or "",
                        path=found.get("path") or "",
                    )
        elif live_used < live_limit:
            found = find_live(item)
            live_used += 1
            if found and found.get("id"):
                with session_scope() as session:
                    repo.upsert_navidrome_link(
                        session,
                        video_id,
                        str(found["id"]),
                        title=found.get("title") or item.get("title") or "",
                        artist=found.get("artist") or item.get("artist") or "",
                        path=found.get("path") or "",
                    )

        if found and found.get("id"):
            item["navidromeId"] = str(found["id"])
            item["inLibrary"] = True
            item["source"] = "both"
        else:
            item.setdefault("navidromeId", None)
            item.setdefault("inLibrary", False)
            item.setdefault("source", "youtube")
        decorated.append(item)
    return decorated


def as_player_tracks(navidrome_songs: list[dict]) -> list[dict]:
    return [map_song(song) if "navidromeId" not in song else song for song in navidrome_songs]
