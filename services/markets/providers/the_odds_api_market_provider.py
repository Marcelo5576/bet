from __future__ import annotations

from .generic_http_market_provider import ConfiguredHttpMarketProvider


class TheOddsApiMarketProvider(ConfiguredHttpMarketProvider):
    def __init__(self) -> None:
        super().__init__(
            name="the_odds_api",
            base_url="https://api.the-odds-api.com/v4",
            api_key_env="THE_ODDS_API_KEY",
            endpoint="/sports/soccer/odds",
            query_key_name="apiKey",
        )
