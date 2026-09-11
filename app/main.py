from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import session as web_session
from app.api.web import router as web_router
from app.core import auth_store
from app.core.icons import ensure_icons
from app.core.logging import logger
from app.core.settings import APP_NAME, APP_VERSION, settings
from app.db.database import init_db
from app.subsonic import media
from app.subsonic.router import router as subsonic_router

STATIC_DIR = "app/static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_icons()
    logger.info(
        "{} {} gestartet - Benutzer '{}', Streaming '{}', Passwort {}",
        APP_NAME,
        APP_VERSION,
        auth_store.username(),
        settings.stream_mode,
        "gesetzt" if auth_store.password_required() else "offen",
    )
    yield
    await media.aclose()


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def attach_session(request: Request, call_next):
    user = web_session.read(request)
    if not user and not auth_store.password_required():
        user = auth_store.username()
    request.state.session_user = user
    return await call_next(request)


app.include_router(subsonic_router)
app.include_router(web_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": APP_VERSION,
            "authenticated": bool(request.state.session_user),
            "password_required": auth_store.password_required(),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(f"{STATIC_DIR}/manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    # Must be served from the root so its scope covers the whole app.
    return FileResponse(
        f"{STATIC_DIR}/sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def apple_icon():
    return FileResponse(f"{STATIC_DIR}/icons/icon-180.png", media_type="image/png")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(f"{STATIC_DIR}/icons/icon.svg", media_type="image/svg+xml")


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/version")
async def version():
    return {"name": APP_NAME, "version": APP_VERSION}
