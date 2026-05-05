from __future__ import annotations

from pathlib import Path
import csv
import json
from typing import Any

from .base import FootballDataSource


class LocalFileFootballDataSource(FootballDataSource):
    name = "CSV/JSON Local"
    provider_type = "local_file"

    def __init__(self, root: str):
        self.root = Path(root)

    def _load_matches(self, filename: str | None = None) -> list[dict[str, Any]]:
        path = self.root / (filename or "football_research_import.json")
        if not path.exists():
            return []
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("matches"), list):
            return payload["matches"]
        return []

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        return self._load_matches(kwargs.get("filename"))

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        return self._load_matches(kwargs.get("filename"))

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._load_matches(kwargs.get("filename")):
            rows.extend(item.get("odds", []))
        return rows

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        return {"provider": self.name, "items": len(self._load_matches(kwargs.get("filename")))}

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        matches = self._load_matches(kwargs.get("filename"))
        return {"provider": self.name, "leagues": sorted({str(item.get("league") or "") for item in matches if item.get("league")})}

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        return []

