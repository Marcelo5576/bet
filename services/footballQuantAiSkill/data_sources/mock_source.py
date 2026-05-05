from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .base import FootballDataSource


class MockFootballDataSource(FootballDataSource):
    name = "Mock Local"
    provider_type = "mock"

    def _matches(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return [
            {
                "external_id": "mock-2026-001",
                "league": "Brasil - Serie A",
                "country": "Brasil",
                "season": 2026,
                "match_date": (now - timedelta(days=4)).isoformat(),
                "home_team": "Flamengo",
                "away_team": "Bahia",
                "status": "FT",
                "home_goals": 2,
                "away_goals": 1,
                "source": "mock",
                "stats": {
                    "possession_home": 58,
                    "possession_away": 42,
                    "shots_home": 15,
                    "shots_away": 8,
                    "shots_on_home": 6,
                    "shots_on_away": 3,
                    "corners_home": 7,
                    "corners_away": 4,
                    "yellow_home": 2,
                    "yellow_away": 3,
                    "red_home": 0,
                    "red_away": 0,
                    "dangerous_attacks_home": 44,
                    "dangerous_attacks_away": 21,
                    "attacks_home": 79,
                    "attacks_away": 54,
                    "xg_home": 1.92,
                    "xg_away": 0.84,
                },
                "odds": [
                    {
                        "timestamp": (now - timedelta(days=4, hours=2)).isoformat(),
                        "market": "match_winner",
                        "home_odd": 1.73,
                        "draw_odd": 3.55,
                        "away_odd": 5.2,
                        "bookmaker": "mock",
                        "source": "mock",
                    },
                    {
                        "timestamp": (now - timedelta(days=4, hours=2)).isoformat(),
                        "market": "totals",
                        "line": "2.5",
                        "over_odd": 1.95,
                        "under_odd": 1.85,
                        "bookmaker": "mock",
                        "source": "mock",
                    },
                ],
            },
            {
                "external_id": "mock-2026-002",
                "league": "Argentina - Liga Profesional",
                "country": "Argentina",
                "season": 2026,
                "match_date": (now - timedelta(days=3)).isoformat(),
                "home_team": "River Plate",
                "away_team": "Talleres",
                "status": "FT",
                "home_goals": 3,
                "away_goals": 1,
                "source": "mock",
                "stats": {
                    "possession_home": 63,
                    "possession_away": 37,
                    "shots_home": 18,
                    "shots_away": 7,
                    "shots_on_home": 8,
                    "shots_on_away": 2,
                    "corners_home": 8,
                    "corners_away": 2,
                    "yellow_home": 1,
                    "yellow_away": 4,
                    "red_home": 0,
                    "red_away": 0,
                    "dangerous_attacks_home": 51,
                    "dangerous_attacks_away": 18,
                    "attacks_home": 88,
                    "attacks_away": 47,
                    "xg_home": 2.24,
                    "xg_away": 0.66,
                },
                "odds": [
                    {
                        "timestamp": (now - timedelta(days=3, hours=3)).isoformat(),
                        "market": "match_winner",
                        "home_odd": 1.66,
                        "draw_odd": 3.7,
                        "away_odd": 5.9,
                        "bookmaker": "mock",
                        "source": "mock",
                    }
                ],
            },
            {
                "external_id": "mock-2026-003",
                "league": "Premier League",
                "country": "England",
                "season": 2026,
                "match_date": (now - timedelta(days=2)).isoformat(),
                "home_team": "Tottenham",
                "away_team": "Brighton",
                "status": "FT",
                "home_goals": 1,
                "away_goals": 1,
                "source": "mock",
                "stats": {
                    "possession_home": 52,
                    "possession_away": 48,
                    "shots_home": 13,
                    "shots_away": 11,
                    "shots_on_home": 4,
                    "shots_on_away": 4,
                    "corners_home": 6,
                    "corners_away": 5,
                    "yellow_home": 2,
                    "yellow_away": 2,
                    "red_home": 0,
                    "red_away": 0,
                    "dangerous_attacks_home": 32,
                    "dangerous_attacks_away": 29,
                    "attacks_home": 64,
                    "attacks_away": 61,
                    "xg_home": 1.11,
                    "xg_away": 1.08,
                },
                "odds": [
                    {
                        "timestamp": (now - timedelta(days=2, hours=1)).isoformat(),
                        "market": "btts",
                        "home_odd": 1.82,
                        "bookmaker": "mock",
                        "source": "mock",
                    }
                ],
            },
            {
                "external_id": "mock-2026-004",
                "league": "La Liga",
                "country": "Spain",
                "season": 2026,
                "match_date": (now - timedelta(days=1)).isoformat(),
                "home_team": "Villarreal",
                "away_team": "Valencia",
                "status": "FT",
                "home_goals": 0,
                "away_goals": 2,
                "source": "mock",
                "stats": {
                    "possession_home": 57,
                    "possession_away": 43,
                    "shots_home": 12,
                    "shots_away": 9,
                    "shots_on_home": 2,
                    "shots_on_away": 5,
                    "corners_home": 7,
                    "corners_away": 3,
                    "yellow_home": 4,
                    "yellow_away": 1,
                    "red_home": 1,
                    "red_away": 0,
                    "dangerous_attacks_home": 28,
                    "dangerous_attacks_away": 31,
                    "attacks_home": 69,
                    "attacks_away": 55,
                    "xg_home": 0.88,
                    "xg_away": 1.41,
                },
                "odds": [
                    {
                        "timestamp": (now - timedelta(days=1, hours=2)).isoformat(),
                        "market": "totals",
                        "line": "2.5",
                        "over_odd": 2.16,
                        "under_odd": 1.69,
                        "bookmaker": "mock",
                        "source": "mock",
                    }
                ],
            },
        ]

    async def getFixtures(self, **kwargs) -> list[dict[str, Any]]:
        return self._matches()

    async def getHistoricalMatches(self, **kwargs) -> list[dict[str, Any]]:
        return self._matches()

    async def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._matches():
            rows.extend(item.get("odds", []))
        return rows

    async def getTeamStats(self, **kwargs) -> dict[str, Any]:
        return {"provider": self.name, "status": "mock", "items": len(self._matches())}

    async def getLeagueStats(self, **kwargs) -> dict[str, Any]:
        return {"provider": self.name, "leagues": sorted({item["league"] for item in self._matches()})}

    async def getInjuries(self, **kwargs) -> list[dict[str, Any]]:
        return []

    async def getStandings(self, **kwargs) -> list[dict[str, Any]]:
        return []

