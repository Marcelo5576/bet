from __future__ import annotations

import httpx
from typing import Any

from .base import FootballDataSource


class TheOddsApiResearchSource(FootballDataSource):
    name = "The Odds API"
    provider_type = "api"

    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            return []
        query = dict(params)
        query["apiKey"] = self.api_key
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}{path}", params=query)
            response.raise_for_status()
            return response.json()

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        sport = kwargs.get("sport") or "soccer_epl"
        return list(await self._get(f"/sports/{sport}/events", {}))

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        sport = kwargs.get("sport") or "soccer_epl"
        event_id = kwargs.get("event_id")
        if event_id:
            return list(await self._get(f"/sports/{sport}/events/{event_id}/odds", {"regions": "eu", "markets": "h2h,totals,btts"}))
        return list(await self._get(f"/sports/{sport}/odds", {"regions": "eu", "markets": "h2h,totals,btts"}))

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        return {}

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        return {}

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        return []

