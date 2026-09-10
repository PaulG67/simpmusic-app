import threading
import time
from typing import Any


class TTLCache:
    """Small thread-safe TTL cache; keeps YouTube Music request volume sane."""

    def __init__(self, ttl: int = 3600, max_items: int = 2000):
        self._ttl = ttl
        self._max_items = max_items
        self._lock = threading.Lock()
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        with self._lock:
            if len(self._data) >= self._max_items:
                # Cheap eviction: drop the entries closest to expiry.
                for stale_key in sorted(self._data, key=lambda k: self._data[k][0])[: self._max_items // 4]:
                    self._data.pop(stale_key, None)
            self._data[key] = (time.monotonic() + (ttl or self._ttl), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
