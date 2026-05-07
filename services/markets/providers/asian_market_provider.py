from __future__ import annotations

from .generic_http_market_provider import ConfiguredHttpMarketProvider


class AsianMarketProvider(ConfiguredHttpMarketProvider):
    def __init__(
        self,
        *,
        base_url: str = "",
        api_key_env: str = "ASIAN_MARKETS_API_KEY",
        endpoint: str = "/markets/live?event_id={event_id}",
    ) -> None:
        super().__init__(
            name="asian_markets",
            base_url=base_url,
            api_key_env=api_key_env,
            endpoint=endpoint,
            header_name="X-API-Key",
        )
