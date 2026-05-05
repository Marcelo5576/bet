from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class FootballDataSource(ABC):
    name: str = "base"
    provider_type: str = "base"

    @abstractmethod
    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        raise NotImplementedError

