from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LiveGame:
    game_id: str
    league: str
    home: str
    away: str
    minute: int
    home_goals: int
    away_goals: int
    home_pressure: int
    away_pressure: int
    home_shots_on: int
    away_shots_on: int
    odds_home: float | None = None
    odds_draw: float | None = None
    odds_away: float | None = None
    priority: int = 50
    division: str = "Outras ligas"
    markets: dict = field(default_factory=dict)


class LiveProvider:
    async def get_live_games(self) -> list[LiveGame]:
        raise NotImplementedError
