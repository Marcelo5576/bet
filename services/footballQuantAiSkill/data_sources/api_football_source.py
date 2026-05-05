from __future__ import annotations

import httpx
from typing import Any

from .base import FootballDataSource


class ApiFootballResearchSource(FootballDataSource):
    name = "API-Football"
    provider_type = "api"

    def __init__(self, base_url: str, api_key: str | None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]] | dict[str, Any]:
        if not self.api_key:
            return []
        headers = {"x-apisports-key": self.api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(f"{self.base_url}{path}", params=params or {}, headers=headers)
            response.raise_for_status()
            body = response.json()
        return body.get("response", body)

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        return list(await self._get("/fixtures", {"date": kwargs.get("date"), "league": kwargs.get("league_id")}))

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        return list(await self._get("/fixtures", {"season": kwargs.get("season"), "league": kwargs.get("league_id"), "status": "FT"}))

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        return list(await self._get("/odds", {"fixture": kwargs.get("fixture_id")}))

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        rows = await self._get("/teams/statistics", {"team": kwargs.get("team_id"), "league": kwargs.get("league_id"), "season": kwargs.get("season")})
        return rows[0] if isinstance(rows, list) and rows else {}

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        return {"provider": self.name, "supported": bool(self.api_key)}

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return list(await self._get("/injuries", {"fixture": kwargs.get("fixture_id")}))

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        return list(await self._get("/standings", {"season": kwargs.get("season"), "league": kwargs.get("league_id")}))

