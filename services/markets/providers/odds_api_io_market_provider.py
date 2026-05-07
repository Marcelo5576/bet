from __future__ import annotations

from .generic_http_market_provider import ConfiguredHttpMarketProvider


class OddsApiIoMarketProvider(ConfiguredHttpMarketProvider):
    def __init__(self) -> None:
        super().__init__(
            name="odds_api_io",
            base_url="https://api.odds-api.io/v3",
            api_key_env="ODDS_API_IO_KEY",
            endpoint="/odds?eventId={event_id}",
            header_name="X-API-Key",
        )
