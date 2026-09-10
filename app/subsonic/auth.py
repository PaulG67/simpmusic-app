"""Subsonic credential handling.

Supports the three schemes clients use in practice: plaintext `p`, hex-encoded
`p=enc:...`, and the salted token `t`/`s` that Amperfy sends by default.
"""

import hashlib
import hmac
from typing import Protocol

from fastapi import Request

from app.core.settings import settings
from app.subsonic.response import (
    ERROR_BAD_CREDENTIALS,
    ERROR_MISSING_PARAM,
    SubsonicError,
)


class ParamSource(Protocol):
    def get(self, name: str, default: str | None = ...) -> str | None: ...


def _decode_password(raw: str) -> str:
    if raw.startswith("enc:"):
        try:
            return bytes.fromhex(raw[4:]).decode("utf-8")
        except ValueError:
            return raw
    return raw


def authenticate(request: Request, params: ParamSource) -> str:
    """`params` covers query string *and* form body, so POSTed credentials
    (the `formPost` extension) authenticate the same way."""
    username = params.get("u")

    # The web UI authenticates with a signed cookie instead of Subsonic params.
    session_user = getattr(request.state, "session_user", None)
    if not username and session_user:
        return session_user

    if not username:
        raise SubsonicError(ERROR_MISSING_PARAM, "Erforderlicher Parameter 'u' fehlt")

    if not hmac.compare_digest(username, settings.subsonic_user):
        raise SubsonicError(ERROR_BAD_CREDENTIALS, "Benutzername oder Passwort falsch")

    token = params.get("t")
    salt = params.get("s")
    password = params.get("p")

    if token and salt:
        expected = hashlib.md5((settings.subsonic_password + salt).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected, token.lower()):
            raise SubsonicError(ERROR_BAD_CREDENTIALS, "Benutzername oder Passwort falsch")
        return username

    if password is not None:
        if not hmac.compare_digest(_decode_password(password), settings.subsonic_password):
            raise SubsonicError(ERROR_BAD_CREDENTIALS, "Benutzername oder Passwort falsch")
        return username

    if params.get("apiKey"):
        if not hmac.compare_digest(params["apiKey"], settings.subsonic_password):
            raise SubsonicError(ERROR_BAD_CREDENTIALS, "API-Key ungültig")
        return username

    raise SubsonicError(ERROR_MISSING_PARAM, "Erforderlicher Parameter 'p' oder 't'/'s' fehlt")
