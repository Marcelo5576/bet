from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any


@dataclass(frozen=True)
class CacheResult:
    key: str
    value: Any
    fresh: bool
    age_seconds: int


@dataclass
class _CacheEntry:
    value: Any
    stored_at: float
    expires_at: float
    stale_until: float


class TTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, key: str, *, allow_stale: bool = False) -> CacheResult | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(str(key))
            if not entry:
                return None
            if entry.stale_until <= now:
                self._entries.pop(str(key), None)
                return None
            fresh = entry.expires_at > now
            if not fresh and not allow_stale:
                return None
            return CacheResult(
                key=str(key),
                value=entry.value,
                fresh=fresh,
                age_seconds=max(0, int(now - entry.stored_at)),
            )

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int,
        *,
        stale_seconds: int | None = None,
    ) -> Any:
        now = time.monotonic()
        ttl_seconds = max(1, int(ttl_seconds))
        stale_seconds = max(ttl_seconds, int(stale_seconds or ttl_seconds * 6))
        with self._lock:
            self._entries[str(key)] = _CacheEntry(
                value=value,
                stored_at=now,
                expires_at=now + ttl_seconds,
                stale_until=now + stale_seconds,
            )
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._entries.pop(str(key), None)

    def purge(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [key for key, entry in self._entries.items() if entry.stale_until <= now]
            for key in expired:
                self._entries.pop(key, None)


_DEFAULT_CACHE = TTLCache()


def get_runtime_cache() -> TTLCache:
    return _DEFAULT_CACHE

