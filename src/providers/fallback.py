from __future__ import annotations

from .base import LiveGame, LiveProvider, provider_label


class FallbackLiveProvider(LiveProvider):
    def __init__(self, *providers: LiveProvider):
        self.providers = [provider for provider in providers if provider is not None]
        self.label = " -> ".join(provider_label(provider) for provider in self.providers)

    async def get_live_games(self) -> list[LiveGame]:
        return await self._run_chain("get_live_games")

    async def get_today_games(self) -> list[LiveGame]:
        return await self._run_chain("get_today_games")

    async def _run_chain(self, method_name: str) -> list[LiveGame]:
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
                return games
        if errors:
            raise RuntimeError(" | ".join(errors))
        return []
