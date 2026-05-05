from __future__ import annotations

from .base import LiveGame, LiveProvider, provider_label
from src.cache import get_runtime_cache


class FallbackLiveProvider(LiveProvider):
    def __init__(
        self,
        *providers: LiveProvider,
        live_ttl_seconds: int = 20,
        today_ttl_seconds: int = 20,
        stale_seconds: int = 120,
    ):
        self.providers = [provider for provider in providers if provider is not None]
        self.live_ttl_seconds = max(5, int(live_ttl_seconds or 20))
        self.today_ttl_seconds = max(5, int(today_ttl_seconds or 20))
        self.stale_seconds = max(self.live_ttl_seconds, int(stale_seconds or 120))
        self.cache = get_runtime_cache()
        self.label = " -> ".join(provider_label(provider) for provider in self.providers)

    async def get_live_games(self) -> list[LiveGame]:
        return await self._run_chain("get_live_games")

    async def get_today_games(self) -> list[LiveGame]:
        return await self._run_chain("get_today_games")

    async def _run_chain(self, method_name: str) -> list[LiveGame]:
        ttl_seconds = self.live_ttl_seconds if method_name == "get_live_games" else self.today_ttl_seconds
        cache_key = f"fallback:{self.label}:{method_name}"
        cached = self.cache.get(cache_key)
        if cached and isinstance(cached.value, list):
            return cached.value
        stale = self.cache.get(cache_key, allow_stale=True)
        errors: list[str] = []
        for provider in self.providers:
            method = getattr(provider, method_name, None)
            if not callable(method):
                continue
            try:
                games = await method()
            except Exception as exc:
                errors.append(f"{provider_label(provider)}: {type(exc).__name__}: {exc}")
                continue
            if games:
                self.cache.set(
                    cache_key,
                    games,
                    ttl_seconds,
                    stale_seconds=max(self.stale_seconds, ttl_seconds * 4),
                )
                return games
        if errors:
            if stale and isinstance(stale.value, list):
                return stale.value
            raise RuntimeError(" | ".join(errors))
        return []
