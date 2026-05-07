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
        self._stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "stale_hits": 0,
            "expired": 0,
            "sets": 0,
            "invalidations": 0,
            "purges": 0,
        }
        self._lock = threading.Lock()

    def get(self, key: str, *, allow_stale: bool = False) -> CacheResult | None:
        now = time.monotonic()
        normalized_key = str(key)
        with self._lock:
            entry = self._entries.get(normalized_key)
            if not entry:
                self._stats["misses"] += 1
                return None
            if entry.stale_until <= now:
                self._entries.pop(normalized_key, None)
                self._stats["expired"] += 1
                self._stats["misses"] += 1
                return None
            fresh = entry.expires_at > now
            if not fresh and not allow_stale:
                self._stats["misses"] += 1
                return None
            if fresh:
                self._stats["hits"] += 1
            else:
                self._stats["stale_hits"] += 1
            return CacheResult(
                key=normalized_key,
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
            self._stats["sets"] += 1
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            if self._entries.pop(str(key), None) is not None:
                self._stats["invalidations"] += 1

    def purge(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [key for key, entry in self._entries.items() if entry.stale_until <= now]
            for key in expired:
                self._entries.pop(key, None)
            self._stats["purges"] += len(expired)

    def stats(self, prefix: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            keys = list(self._entries.keys())
            if prefix:
                prefix_text = str(prefix)
                keys = [key for key in keys if key.startswith(prefix_text)]
            fresh = 0
            stale = 0
            expired = 0
            for key in keys:
                entry = self._entries.get(key)
                if not entry:
                    continue
                if entry.stale_until <= now:
                    expired += 1
                elif entry.expires_at > now:
                    fresh += 1
                else:
                    stale += 1
            hits = int(self._stats.get("hits", 0))
            misses = int(self._stats.get("misses", 0))
            stale_hits = int(self._stats.get("stale_hits", 0))
            total_reads = hits + misses + stale_hits
            cache_hits = hits + stale_hits
            hit_ratio = round(cache_hits / total_reads, 4) if total_reads else 0.0
            return {
                "entries": len(keys),
                "fresh_entries": fresh,
                "stale_entries": stale,
                "expired_entries": expired,
                "hits": hits,
                "misses": misses,
                "stale_hits": stale_hits,
                "sets": int(self._stats.get("sets", 0)),
                "invalidations": int(self._stats.get("invalidations", 0)),
                "purges": int(self._stats.get("purges", 0)),
                "cache_hit_ratio": hit_ratio,
            }

    def reset_stats(self) -> None:
        with self._lock:
            for key in self._stats:
                self._stats[key] = 0


_DEFAULT_CACHE = TTLCache()


def get_runtime_cache() -> TTLCache:
    return _DEFAULT_CACHE
