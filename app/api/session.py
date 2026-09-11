"""Cookie sessions for the built-in web player.

Subsonic clients authenticate per request with `u`/`t`/`s`; the browser instead
gets a signed cookie so the `<audio>` element can hit the stream endpoint
without credentials in the URL.
"""

import hashlib
import hmac

from fastapi import Request
from itsdangerous import URLSafeTimedSerializer

from app.core.auth_store import password as stored_password
from app.core.auth_store import password_required
from app.core.settings import clean_secret, settings

COOKIE_NAME = "music_play_session"
MAX_AGE = 60 * 60 * 24 * 90

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="music-play-session")


def secrets_equal(left: str | None, right: str | None) -> bool:
    """Length-safe comparison. hmac.compare_digest raises if sizes differ."""
    digest = hashlib.sha256
    a = digest((left or "").encode("utf-8")).digest()
    b = digest((right or "").encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def check_password(password: str) -> bool:
    if not password_required():
        return True
    return secrets_equal(clean_secret(password), stored_password())


def issue(username: str) -> str:
    return _serializer.dumps({"u": username})


def read(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        header = request.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
    except Exception:
        return None
    return data.get("u")
