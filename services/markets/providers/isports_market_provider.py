from __future__ import annotations

from .generic_http_market_provider import ConfiguredHttpMarketProvider


class ISportsMarketProvider(ConfiguredHttpMarketProvider):
    def __init__(self) -> None:
        super().__init__(
            name="isports",
            base_url="https://api.isportsapi.com",
            api_key_env="ISPORTS_API_KEY",
            endpoint="/sport/football/odds?matchId={event_id}",
            query_key_name="api_key",
        )
