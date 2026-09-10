"""Cookie sessions for the built-in web player.

Subsonic clients authenticate per request with `u`/`t`/`s`; the browser instead
gets a signed cookie so the `<audio>` element can hit the stream endpoint
without credentials in the URL.
"""

import hmac

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.settings import settings

COOKIE_NAME = "simpmusic_session"
MAX_AGE = 60 * 60 * 24 * 90

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="simpmusic-session")


def check_password(password: str) -> bool:
    return hmac.compare_digest(password or "", settings.subsonic_password)


def issue(username: str) -> str:
    return _serializer.dumps({"u": username})


def read(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("u")
