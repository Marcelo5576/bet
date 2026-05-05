from __future__ import annotations

import httpx
from typing import Any

from .base import FootballDataSource


class StatsBombOpenResearchSource(FootballDataSource):
    name = "StatsBomb Open Data"
    provider_type = "open_data"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def _get_json(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}/{path.lstrip('/')}")
            response.raise_for_status()
            return response.json()

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        competition_id = kwargs.get("competition_id")
        season_id = kwargs.get("season_id")
        if not competition_id or not season_id:
            return []
        return list(await self._get_json(f"data/matches/{competition_id}/{season_id}.json"))

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        return await self.getFixtures(**kwargs)

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        return {}

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        return {}

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        return []

