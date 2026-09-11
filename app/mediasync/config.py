"""MediaSync-Hub connection, stored like Navidrome in the config share."""

from __future__ import annotations

import json
import threading

from app.core.settings import CONFIG_DIR, clean_secret, settings

CONFIG_FILE = CONFIG_DIR / "mediasync.json"
_lock = threading.Lock()
_cache: dict | None = None


def _defaults() -> dict:
    return {
        "url": (getattr(settings, "mediasync_url", "") or "").rstrip("/"),
        "username": clean_secret(getattr(settings, "mediasync_user", "") or ""),
        "password": clean_secret(getattr(settings, "mediasync_password", "") or ""),
    }


def _read_unlocked() -> dict:
    global _cache
    if _cache is not None:
        return dict(_cache)

    data = _defaults()
    if CONFIG_FILE.exists():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if loaded.get("url") is not None:
                    data["url"] = clean_secret(str(loaded.get("url") or "")).rstrip("/")
                if loaded.get("username") is not None:
                    data["username"] = clean_secret(str(loaded.get("username") or ""))
                if loaded.get("password") is not None:
                    data["password"] = clean_secret(str(loaded.get("password") or ""))
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    _cache = dict(data)
    return dict(_cache)


def load() -> dict:
    with _lock:
        return _read_unlocked()


def save(payload: dict) -> dict:
    global _cache
    current = load()
    password = payload.get("password")
    if password is None:
        password = current.get("password") or ""
    data = {
        "url": clean_secret(str(payload.get("url") or "")).rstrip("/"),
        "username": clean_secret(str(payload.get("username") or "")),
        "password": clean_secret(str(password or "")),
    }
    with _lock:
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _cache = dict(data)
    return dict(data)


def configured() -> bool:
    return bool(load().get("url"))


def public_view() -> dict:
    data = load()
    return {
        "configured": configured(),
        "url": data.get("url") or "",
        "username": data.get("username") or "",
        "passwordSet": bool(data.get("password")),
    }
