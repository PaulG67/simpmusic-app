"""App-managed credentials stored in the config share.

Unraid env passwords have been unreliable (empty fields, quoting, $VAR).
The first-run default is therefore: no password. The user can set one later
under Mehr. Subsonic/Amperfy use the same store.
"""

from __future__ import annotations

import json
import threading

from app.core.settings import CONFIG_DIR, clean_secret, settings

AUTH_FILE = CONFIG_DIR / "auth.json"
_lock = threading.Lock()
_cache: dict | None = None


def _defaults() -> dict:
    return {
        "username": settings.subsonic_user or "musicplay",
        "password": "",
    }


def _read_unlocked() -> dict:
    global _cache
    if _cache is not None:
        return dict(_cache)

    data = _defaults()
    if AUTH_FILE.exists():
        try:
            loaded = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data["username"] = clean_secret(str(loaded.get("username") or data["username"])) or data["username"]
                data["password"] = clean_secret(str(loaded.get("password") or ""))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    else:
        try:
            AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    _cache = dict(data)
    return dict(_cache)


def load() -> dict:
    with _lock:
        return _read_unlocked()


def save(username: str, password: str) -> dict:
    global _cache
    data = {
        "username": clean_secret(username) or "musicplay",
        "password": clean_secret(password),
    }
    with _lock:
        AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _cache = dict(data)
    return dict(data)


def username() -> str:
    return load()["username"]


def password() -> str:
    return load()["password"]


def password_required() -> bool:
    return bool(password())
