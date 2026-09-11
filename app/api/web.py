"""JSON API consumed by the built-in web player."""

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.api import session as web_session
from app.core import auth_store
from app.core.logging import logger
from app.core.settings import APP_VERSION, settings
from app.db import repo
from app.db.database import session_scope
from app.services import catalog, extras, stats
from app.services import translate as translate_service
from app.subsonic import media
from app.ytm import ids
from app.ytm import client as ytm
from app.ytm import streams

router = APIRouter(prefix="/api")


def _require_session(request: Request) -> str:
    username = getattr(request.state, "session_user", None)
    if not username:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return username


def _subsonic_id(item: dict, kind: str) -> str:
    if kind == "song":
        return ids.song_id(item["id"])
    if kind == "album":
        return ids.album_id(item["id"])
    if kind == "artist":
        return ids.artist_id(item.get("id"), item.get("name", ""))
    if kind == "podcast":
        return ids.podcast_id(item["id"])
    return item["id"]


def _decorate(items: list[dict], kind: str | None = None) -> list[dict]:
    """Attach the Subsonic id and favourite flag the frontend needs.

    With `kind` omitted, each item's own `kind` is used, which is how mixed
    home/chart sections arrive.
    """
    if not items:
        return []

    with session_scope() as session:
        starred = repo.starred_map(session)

    decorated = []
    for item in items:
        item_kind = kind or item.get("kind") or "song"
        subsonic_id = _subsonic_id(item, item_kind)
        decorated.append({**item, "sid": subsonic_id, "starred": subsonic_id in starred, "kind": item_kind})
    return decorated


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


@router.get("/login-hint")
async def login_hint():
    required = auth_store.password_required()
    return {
        "username": auth_store.username(),
        "passwordRequired": required,
        "passwordLength": len(auth_store.password()) if required else 0,
        "version": APP_VERSION,
    }


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        web_session.COOKIE_NAME,
        token,
        max_age=web_session.MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https",
    )


def _session_payload(user: str | None) -> dict:
    required = auth_store.password_required()
    return {
        "authenticated": bool(user) or not required,
        "passwordRequired": required,
        "user": user or (auth_store.username() if not required else None),
        "version": APP_VERSION,
        "ytmAccount": ytm.is_authenticated(),
        "location": settings.ytm_location,
    }


@router.post("/login")
async def login(request: Request):
    content_type = (request.headers.get("content-type") or "").lower()
    wants_json = "application/json" in content_type
    if wants_json:
        payload = await request.json()
    else:
        form = await request.form()
        payload = {"password": form.get("password")}

    password = payload.get("password") or ""
    entered = web_session.clean_secret(password)
    expected = auth_store.password()
    if auth_store.password_required() and not web_session.check_password(password):
        logger.warning(
            "Login fehlgeschlagen: Eingabe {} Zeichen, konfiguriert {} Zeichen",
            len(entered),
            len(expected),
        )
        detail = (
            f"Passwort falsch. Eingegeben: {len(entered)} Zeichen, "
            f"gesetzt: {len(expected)} Zeichen."
        )
        if wants_json:
            raise HTTPException(status_code=401, detail=detail)
        return RedirectResponse("/?login=fail", status_code=303)

    token = web_session.issue(auth_store.username())
    logger.info("Web-Login erfolgreich")
    if wants_json:
        response = JSONResponse(
            {"user": auth_store.username(), "token": token, "version": APP_VERSION}
        )
        _set_session_cookie(response, token, request)
        return response

    redirect = RedirectResponse("/", status_code=303)
    _set_session_cookie(redirect, token, request)
    return redirect


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(web_session.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/session")
async def whoami(request: Request):
    return _session_payload(getattr(request.state, "session_user", None))


@router.post("/account")
async def save_account(request: Request, response: Response, payload: dict = Body(...)):
    """Set or clear the in-app password. Open on first run until a password exists."""
    if auth_store.password_required():
        _require_session(request)

    username = web_session.clean_secret(payload.get("username") or "") or auth_store.username()
    if payload.get("password") is None:
        raise HTTPException(status_code=400, detail="Passwort fehlt")
    new_password = web_session.clean_secret(str(payload.get("password")))
    confirm = web_session.clean_secret(str(payload.get("passwordConfirm") or new_password))
    if new_password != confirm:
        raise HTTPException(status_code=400, detail="Passwörter stimmen nicht überein")

    auth_store.save(username, new_password)
    token = web_session.issue(username)
    _set_session_cookie(response, token, request)
    logger.info("Zugang aktualisiert - Passwort {}", "gesetzt" if new_password else "entfernt")
    return {"ok": True, "user": username, "token": token, "passwordRequired": bool(new_password)}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@router.get("/home")
async def home(request: Request):
    _require_session(request)
    sections = await catalog.home()
    if not sections:
        sections = await catalog.charts()
    return {"sections": [{"title": s["title"], "items": _decorate(s["items"])} for s in sections]}


@router.get("/charts")
async def charts(request: Request):
    _require_session(request)
    sections = await catalog.charts()
    return {"sections": [{"title": s["title"], "items": _decorate(s["items"])} for s in sections]}


@router.get("/search")
async def search(request: Request, q: str = "", scope: str = "all", limit: int = 25):
    _require_session(request)
    if not q.strip():
        return {"songs": [], "albums": [], "artists": []}

    if scope == "all":
        results = await catalog.search(q, song_limit=limit, album_limit=12, artist_limit=12)
        return {
            "songs": _decorate(results["songs"], "song"),
            "albums": _decorate(results["albums"], "album"),
            "artists": _decorate(results["artists"], "artist"),
        }

    items = await catalog.search_scope(q, scope, limit)
    kind = {"songs": "song", "albums": "album", "artists": "artist", "playlists": "playlist"}.get(scope, "song")
    return {scope: _decorate(items, kind)}


@router.get("/suggest")
async def suggest(request: Request, q: str = ""):
    _require_session(request)
    return {"suggestions": await catalog.suggestions(q)}


@router.get("/album/{album_id}")
async def album(request: Request, album_id: str):
    _require_session(request)
    data = await catalog.get_album(album_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Album nicht gefunden")
    return {**data, "sid": ids.album_id(album_id), "tracks": _decorate(data.get("tracks") or [], "song")}


@router.get("/artist/{artist_id}")
async def artist(request: Request, artist_id: str):
    _require_session(request)
    data = await catalog.get_artist(artist_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Interpret nicht gefunden")
    return {
        **data,
        "sid": ids.artist_id(artist_id, data.get("name", "")),
        "albums": _decorate(data.get("albums") or [], "album"),
        "top_songs": _decorate(data.get("top_songs") or [], "song"),
        "related": _decorate(data.get("related") or [], "artist"),
    }


@router.get("/ytplaylist/{playlist_id}")
async def yt_playlist(request: Request, playlist_id: str):
    _require_session(request)
    data = await catalog.get_ytm_playlist(playlist_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Playlist nicht gefunden")
    return {**data, "tracks": _decorate(data.get("tracks") or [], "song")}


@router.get("/radio/{video_id}")
async def radio(request: Request, video_id: str, limit: int = 30):
    _require_session(request)
    return {"tracks": _decorate(await catalog.radio(video_id, limit), "song")}


@router.get("/lyrics/{video_id}")
async def lyrics(request: Request, video_id: str, translate: bool = False, lang: str = ""):
    _require_session(request)
    found = await catalog.lyrics(video_id)
    if not found:
        return {"text": "", "synced": None, "source": "", "translation": None}

    translation = None
    if translate:
        translation = await translate_service.translate_lyrics(video_id, found, lang or None)

    return {
        **found,
        "translation": translation,
        "translationAvailable": settings.translation_enabled,
        "translationLanguage": settings.translate_language,
    }


@router.get("/votes/{video_id}")
async def votes(request: Request, video_id: str):
    """Like/dislike counts via ReturnYouTubeDislike."""
    _require_session(request)
    return await extras.votes(video_id) or {}


# --------------------------------------------------------------------------
# Moods, genres and podcasts
# --------------------------------------------------------------------------


@router.get("/moods")
async def moods(request: Request):
    _require_session(request)
    return {"sections": await catalog.mood_categories()}


@router.get("/mood")
async def mood(request: Request, params: str):
    _require_session(request)
    return {"playlists": _decorate(await catalog.mood_playlists(params), "playlist")}


@router.get("/podcasts")
async def podcasts(request: Request):
    _require_session(request)
    with session_scope() as session:
        subscribed = repo.subscribed_podcasts(session)
    return {
        "subscribed": _decorate([{**item, "name": item["title"]} for item in subscribed], "podcast"),
        "newEpisodes": _decorate(await catalog.new_episodes(), "song"),
    }


@router.get("/podcasts/search")
async def search_podcasts(request: Request, q: str, limit: int = 20):
    _require_session(request)
    shows = await catalog.search_podcasts(q, limit)
    episodes = await catalog.search_episodes(q, limit)
    return {
        "podcasts": _decorate([{**item, "name": item["title"]} for item in shows], "podcast"),
        "episodes": _decorate(episodes, "song"),
    }


@router.get("/podcast/{podcast_id}")
async def podcast(request: Request, podcast_id: str):
    _require_session(request)
    found = await catalog.get_podcast(podcast_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Podcast nicht gefunden")

    subsonic_id = ids.podcast_id(podcast_id)
    with session_scope() as session:
        subscribed = repo.is_starred(session, subsonic_id, "podcast") is not None

    return {
        **found,
        "sid": subsonic_id,
        "subscribed": subscribed,
        "episodes": _decorate(found.get("episodes") or [], "song"),
    }


@router.post("/podcast/{podcast_id}/subscribe")
async def subscribe_podcast(request: Request, podcast_id: str, payload: dict = Body({})):
    _require_session(request)
    subscribe = payload.get("subscribed", True)
    subsonic_id = ids.podcast_id(podcast_id)

    if subscribe:
        await catalog.get_podcast(podcast_id)

    with session_scope() as session:
        if subscribe:
            repo.star(session, subsonic_id, "podcast")
        else:
            repo.unstar(session, subsonic_id, "podcast")

    return {"sid": subsonic_id, "subscribed": bool(subscribe)}


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


@router.get("/stats")
async def listening_stats(request: Request, limit: int = 10):
    _require_session(request)
    with session_scope() as session:
        return stats.overview(session, limit)


@router.get("/wrapped/{year}")
async def wrapped(request: Request, year: int):
    _require_session(request)
    with session_scope() as session:
        return stats.wrapped(session, year)


# --------------------------------------------------------------------------
# Player preferences (equaliser, crossfade, sleep timer)
# --------------------------------------------------------------------------


@router.get("/settings")
async def read_settings(request: Request):
    _require_session(request)
    with session_scope() as session:
        stored = repo.all_settings(session)
    return {
        "player": stored.get("player", {}),
        "equalizer": stored.get("equalizer", {}),
        "translationAvailable": settings.translation_enabled,
        "translationLanguage": settings.translate_language,
        "sponsorblock": settings.sponsorblock_enabled,
        "dislikes": settings.return_youtube_dislike,
    }


@router.put("/settings/{key}")
async def write_settings(request: Request, key: str, payload: dict = Body(...)):
    _require_session(request)
    if key not in ("player", "equalizer"):
        raise HTTPException(status_code=400, detail="Unbekannter Einstellungsbereich")
    with session_scope() as session:
        repo.set_setting(session, key, payload)
    return {"ok": True}


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------


@router.get("/favorites")
async def favorites(request: Request):
    _require_session(request)
    with session_scope() as session:
        songs = repo.starred_tracks(session)
        albums = repo.starred_albums(session)
        artists = repo.starred_artists(session)
        recent = repo.recently_played_tracks(session, 60)
        most = repo.most_played_tracks(session, 60)
    return {
        "songs": _decorate(songs, "song"),
        "albums": _decorate(albums, "album"),
        "artists": _decorate(artists, "artist"),
        "recent": _decorate(recent, "song"),
        "most": _decorate(most, "song"),
    }


@router.post("/favorite")
async def toggle_favorite(request: Request, payload: dict = Body(...)):
    _require_session(request)
    subsonic_id = payload.get("sid")
    if not subsonic_id:
        raise HTTPException(status_code=400, detail="sid fehlt")

    kind, value = ids.parse(subsonic_id)
    item_type = {ids.SONG: "song", ids.ALBUM: "album", ids.ARTIST: "artist"}.get(kind)
    if item_type is None:
        raise HTTPException(status_code=400, detail="Unbekannter Typ")

    starred = bool(payload.get("starred"))
    if starred:
        # Metadata must exist locally before the item joins the library.
        if item_type == "song":
            await catalog.get_song(value)
        elif item_type == "album":
            await catalog.get_album(value)
        else:
            channel_id, _ = ids.artist_payload(subsonic_id)
            if channel_id:
                await catalog.get_artist(channel_id)

    with session_scope() as session:
        if starred:
            repo.star(session, subsonic_id, item_type)
        else:
            repo.unstar(session, subsonic_id, item_type)

    return {"sid": subsonic_id, "starred": starred}


@router.get("/playlists")
async def playlists(request: Request):
    _require_session(request)
    with session_scope() as session:
        items = [repo.playlist_summary(session, playlist) for playlist in repo.list_playlists(session)]
    return {"playlists": items, "account": await catalog.account_playlists()}


@router.get("/playlist/{playlist_id}")
async def playlist(request: Request, playlist_id: int):
    _require_session(request)
    with session_scope() as session:
        found = repo.get_playlist(session, playlist_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Playlist nicht gefunden")
        summary = repo.playlist_summary(session, found)
        tracks = repo.playlist_tracks(session, playlist_id)
    return {**summary, "tracks": _decorate(tracks, "song")}


@router.post("/playlists")
async def create_playlist(request: Request, payload: dict = Body(...)):
    _require_session(request)
    video_ids = [ids.parse(sid)[1] for sid in payload.get("tracks", []) if ids.parse(sid)[0] == ids.SONG]
    await catalog.get_songs(video_ids)
    with session_scope() as session:
        created = repo.create_playlist(session, payload.get("name", "Neue Playlist"), video_ids)
        session.flush()
        summary = repo.playlist_summary(session, created)
    return summary


@router.post("/playlist/{playlist_id}/tracks")
async def add_to_playlist(request: Request, playlist_id: int, payload: dict = Body(...)):
    _require_session(request)
    video_ids = [ids.parse(sid)[1] for sid in payload.get("tracks", []) if ids.parse(sid)[0] == ids.SONG]
    await catalog.get_songs(video_ids)
    with session_scope() as session:
        if repo.get_playlist(session, playlist_id) is None:
            raise HTTPException(status_code=404, detail="Playlist nicht gefunden")
        if payload.get("replace"):
            repo.replace_playlist_tracks(session, playlist_id, video_ids)
        else:
            repo.append_playlist_tracks(session, playlist_id, video_ids)
    return {"ok": True}


@router.delete("/playlist/{playlist_id}")
async def remove_playlist(request: Request, playlist_id: int):
    _require_session(request)
    with session_scope() as session:
        repo.delete_playlist(session, playlist_id)
    return {"ok": True}


@router.post("/scrobble")
async def scrobble(request: Request, payload: dict = Body(...)):
    _require_session(request)
    video_id = payload.get("videoId")
    if not video_id:
        raise HTTPException(status_code=400, detail="videoId fehlt")
    await catalog.get_song(video_id)
    with session_scope() as session:
        repo.record_play(session, video_id, "web", seconds=int(payload.get("seconds") or 0))
    return {"ok": True}


# --------------------------------------------------------------------------
# Extras
# --------------------------------------------------------------------------


@router.get("/sponsorblock/{video_id}")
async def sponsorblock(request: Request, video_id: str):
    """Non-music segments (intros, outros) so the web player can skip them."""
    _require_session(request)
    return {"segments": await extras.skip_segments(video_id)}


@router.get("/status")
async def status(request: Request):
    _require_session(request)
    with session_scope() as session:
        library = len(repo.library_track_ids(session))
    return {
        "version": APP_VERSION,
        "libraryTracks": library,
        "cache": streams.cache_stats(),
        "streamMode": settings.stream_mode,
        "subsonicUser": auth_store.username(),
        "passwordRequired": auth_store.password_required(),
        "translation": {
            "enabled": settings.translation_enabled,
            "provider": settings.translate_provider,
            "language": settings.translate_language,
        },
        "sponsorblock": settings.sponsorblock_enabled,
        "dislikes": settings.return_youtube_dislike,
        "ytmAccount": ytm.is_authenticated(),
    }


@router.post("/cache/clear")
async def clear_cache(request: Request):
    _require_session(request)
    streams.clear_disk_cache()
    ytm.clear_cache()
    return {"ok": True}


# --------------------------------------------------------------------------
# Playback for the web player (cookie authenticated)
# --------------------------------------------------------------------------


@router.get("/stream/{video_id}")
async def stream(request: Request, video_id: str):
    _require_session(request)
    return await media.stream_track(request, video_id)


@router.get("/cover")
async def cover(request: Request, u: str, size: int = 544):
    """Proxy artwork through the server.

    Two reasons: the browser never talks to Google directly, and same-origin
    responses can be stored in the Cache API for offline playback (opaque
    cross-origin responses cannot).
    """
    _require_session(request)
    if not u.startswith("https://"):
        raise HTTPException(status_code=400, detail="Ungültige Cover-URL")
    return await media.fetch_cover(u, size)
