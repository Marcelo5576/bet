from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
import time
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class UserCooldownStatus:
    active: bool
    wait_seconds: int = 0
    reason: str = ""


class RequestQueueService:
    """Small async request coordinator for provider calls.

    It coalesces identical in-flight requests, limits provider concurrency and
    keeps per-user cooldowns for expensive AI providers.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Task[Any]] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._user_cooldowns: dict[str, tuple[float, str]] = {}
        self._stats: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()

    async def run(
        self,
        provider: str,
        key: str,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        max_concurrency: int = 4,
    ) -> Any:
        provider_name = self._normalize(provider)
        request_key = f"{provider_name}:{str(key)}"
        task = self._get_in_flight(request_key, provider_name)
        if task is not None:
            return await task

        semaphore = self._get_semaphore(provider_name, max_concurrency)

        async def _wrapped() -> Any:
            self._bump(provider_name, "started")
            async with semaphore:
                try:
                    result = await coro_factory()
                    self._bump(provider_name, "completed")
                    return result
                except Exception:
                    self._bump(provider_name, "failed")
                    raise
                finally:
                    with self._lock:
                        self._in_flight.pop(request_key, None)

        created: asyncio.Task[Any] | None = None
        existing_task: asyncio.Task[Any] | None = None
        with self._lock:
            existing = self._in_flight.get(request_key)
            if existing is not None:
                self._bump(provider_name, "coalesced")
                existing_task = existing
            else:
                created = asyncio.create_task(_wrapped())
                self._in_flight[request_key] = created
        if existing_task is not None:
            return await existing_task
        if created is None:
            raise RuntimeError("fila de requests nao criou tarefa")
        return await created

    def cooldown_user(self, provider: str, user_id: str | int | None, seconds: int, *, reason: str = "") -> None:
        if user_id is None:
            return
        wait_seconds = max(1, int(seconds or 1))
        key = self._user_key(provider, user_id)
        with self._lock:
            expires_at = time.monotonic() + wait_seconds
            current = self._user_cooldowns.get(key)
            if current is None or current[0] < expires_at:
                self._user_cooldowns[key] = (expires_at, str(reason or "cooldown ativo"))
            self._bump(self._normalize(provider), "user_cooldowns")

    def user_status(self, provider: str, user_id: str | int | None) -> UserCooldownStatus:
        if user_id is None:
            return UserCooldownStatus(active=False)
        key = self._user_key(provider, user_id)
        now = time.monotonic()
        with self._lock:
            current = self._user_cooldowns.get(key)
            if not current:
                return UserCooldownStatus(active=False)
            expires_at, reason = current
            if expires_at <= now:
                self._user_cooldowns.pop(key, None)
                return UserCooldownStatus(active=False)
            return UserCooldownStatus(
                active=True,
                wait_seconds=max(1, int(expires_at - now)),
                reason=reason,
            )

    def stats(self, provider: str | None = None) -> dict[str, Any]:
        with self._lock:
            provider_filter = self._normalize(provider) if provider else None
            if provider_filter:
                raw = dict(self._stats.get(provider_filter, {}))
                raw["in_flight"] = sum(1 for key in self._in_flight if key.startswith(f"{provider_filter}:"))
                return raw
            payload: dict[str, Any] = {}
            for name, stats in self._stats.items():
                row = dict(stats)
                row["in_flight"] = sum(1 for key in self._in_flight if key.startswith(f"{name}:"))
                payload[name] = row
            return payload

    def _get_in_flight(self, request_key: str, provider: str) -> asyncio.Task[Any] | None:
        with self._lock:
            task = self._in_flight.get(request_key)
            if task is not None and not task.done():
                self._bump(provider, "coalesced")
                return task
            if task is not None:
                self._in_flight.pop(request_key, None)
            return None

    def _get_semaphore(self, provider: str, max_concurrency: int) -> asyncio.Semaphore:
        with self._lock:
            semaphore = self._semaphores.get(provider)
            if semaphore is None:
                semaphore = asyncio.Semaphore(max(1, int(max_concurrency or 1)))
                self._semaphores[provider] = semaphore
            return semaphore

    def _bump(self, provider: str, metric: str) -> None:
        stats = self._stats.setdefault(provider, {})
        stats[metric] = int(stats.get(metric, 0)) + 1

    def _user_key(self, provider: str, user_id: str | int) -> str:
        return f"{self._normalize(provider)}:{str(user_id).strip().lower()}"

    def _normalize(self, value: str | None) -> str:
        return str(value or "provider").strip().lower() or "provider"


_DEFAULT_REQUEST_QUEUE = RequestQueueService()


def get_request_queue_service() -> RequestQueueService:
    return _DEFAULT_REQUEST_QUEUE
