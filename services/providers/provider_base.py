from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    active: bool
    last_success_at: str | None = None
    last_error: str | None = None
    rate_limit_429: bool = False
    cache_hit_ratio: float = 0.0
    markets_available: list[str] = field(default_factory=list)


class ProviderBase(Protocol):
    name: str

    async def get_live_events(self) -> list[dict[str, Any]]:
        ...

    async def get_prematch_events(self) -> list[dict[str, Any]]:
        ...

    async def get_live_odds(self) -> list[dict[str, Any]]:
        ...

    async def get_prematch_odds(self) -> list[dict[str, Any]]:
        ...

    async def get_event_stats(self, event_id: str) -> dict[str, Any]:
        ...

    async def get_corners(self, event_id: str) -> list[dict[str, Any]]:
        ...

    async def get_cards(self, event_id: str) -> list[dict[str, Any]]:
        ...

    async def get_asian_lines(self, event_id: str) -> list[dict[str, Any]]:
        ...
