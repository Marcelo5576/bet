from __future__ import annotations

import httpx
from typing import Any

from .base import FootballDataSource


class FootballDataOrgResearchSource(FootballDataSource):
    name = "Football-Data.org"
    provider_type = "api"

    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _get(self, path: str) -> dict[str, Any]:
        if not self.api_key:
            return {}
        headers = {"X-Auth-Token": self.api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}{path}", headers=headers)
            response.raise_for_status()
            return response.json()

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        competition = kwargs.get("competition") or "PL"
        data = await self._get(f"/competitions/{competition}/matches")
        return data.get("matches", [])

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        competition = kwargs.get("competition") or "PL"
        season = kwargs.get("season")
        suffix = f"?season={season}" if season else ""
        data = await self._get(f"/competitions/{competition}/matches{suffix}")
        return data.get("matches", [])

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        team_id = kwargs.get("team_id")
        if not team_id:
            return {}
        return await self._get(f"/teams/{team_id}")

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        competition = kwargs.get("competition") or "PL"
        return await self._get(f"/competitions/{competition}")

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        competition = kwargs.get("competition") or "PL"
        data = await self._get(f"/competitions/{competition}/standings")
        return data.get("standings", [])

