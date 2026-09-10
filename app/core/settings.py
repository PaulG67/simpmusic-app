import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

APP_NAME = "SimpMusic-App"
APP_VERSION = "1.0.0"
SUBSONIC_API_VERSION = "1.16.1"

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/cache"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/logs"))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _session_secret() -> str:
    """Stable across restarts so browser sessions survive a container update."""
    from_env = os.getenv("SESSION_SECRET", "").strip()
    if from_env:
        return from_env

    secret_file = CONFIG_DIR / "session.secret"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()

    generated = secrets.token_urlsafe(48)
    secret_file.write_text(generated, encoding="utf-8")
    return generated


class Settings(BaseModel):
    app_name: str = APP_NAME
    app_version: str = APP_VERSION

    subsonic_user: str = os.getenv("SUBSONIC_USER", "simpmusic")
    subsonic_password: str = os.getenv("SUBSONIC_PASSWORD", "simpmusic")
    session_secret: str = ""

    server_url: str = os.getenv("SERVER_URL", "").rstrip("/")

    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{CONFIG_DIR / 'simpmusic.db'}")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    ytm_language: str = os.getenv("YTM_LANGUAGE", "de")
    ytm_location: str = os.getenv("YTM_LOCATION", "CH")
    ytm_auth_file: str = os.getenv("YTM_AUTH_FILE", "")

    stream_mode: str = os.getenv("STREAM_MODE", "proxy").lower()
    cache_enabled: bool = _bool("CACHE_ENABLED", True)
    cache_max_gb: float = float(os.getenv("CACHE_MAX_GB", "10"))
    transcode_to_aac: bool = _bool("TRANSCODE_TO_AAC", True)

    ytdlp_player_clients: str = os.getenv("YTDLP_PLAYER_CLIENTS", "")
    ytdlp_cookie_file: str = os.getenv("YTDLP_COOKIE_FILE", "")

    # Lyrics translation: "none", "libretranslate" or "openai" (any
    # OpenAI-compatible endpoint, e.g. Ollama or LM Studio).
    translate_provider: str = os.getenv("TRANSLATE_PROVIDER", "none").lower()
    translate_url: str = os.getenv("TRANSLATE_URL", "").rstrip("/")
    translate_api_key: str = os.getenv("TRANSLATE_API_KEY", "")
    translate_model: str = os.getenv("TRANSLATE_MODEL", "gpt-4o-mini")
    translate_target: str = os.getenv("TRANSLATE_TARGET", "")

    return_youtube_dislike: bool = _bool("RETURN_YOUTUBE_DISLIKE", True)
    sponsorblock_enabled: bool = _bool("SPONSORBLOCK", True)

    metadata_ttl: int = int(os.getenv("METADATA_TTL", "3600"))
    stream_url_margin: int = int(os.getenv("STREAM_URL_MARGIN", "600"))

    @property
    def translate_language(self) -> str:
        return self.translate_target or self.ytm_language or "de"

    @property
    def translation_enabled(self) -> bool:
        return self.translate_provider in ("libretranslate", "openai") and bool(self.translate_url)

    @property
    def audio_cache_dir(self) -> Path:
        return CACHE_DIR / "audio"

    @property
    def cover_cache_dir(self) -> Path:
        return CACHE_DIR / "covers"


for _directory in (CONFIG_DIR, CACHE_DIR, LOG_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

settings = Settings(session_secret=_session_secret())

settings.audio_cache_dir.mkdir(parents=True, exist_ok=True)
settings.cover_cache_dir.mkdir(parents=True, exist_ok=True)
