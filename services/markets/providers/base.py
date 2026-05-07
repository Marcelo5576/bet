from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderMarketPayload:
    provider: str
    event_id: str
    markets: list[dict[str, Any]] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    is_real: bool = True
    error: str | None = None


class MarketProvider(Protocol):
    name: str

    async def get_live_markets(self, event_id: str | None = None) -> ProviderMarketPayload:
        ...
