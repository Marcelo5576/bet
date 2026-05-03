from __future__ import annotations

from random import randint, random

from .base import LiveGame, LiveProvider


class MockProvider(LiveProvider):
    label = "MockProvider"

    async def get_live_games(self) -> list[LiveGame]:
        minute = randint(12, 72)
        pressure_boost = randint(0, 18)
        return [
            LiveGame(
                game_id="mock-ars-che",
                league="England - Premier Demo",
                home="Arsenal Demo",
                away="Chelsea Demo",
                minute=minute,
                home_goals=0,
                away_goals=0,
                home_pressure=64 + pressure_boost,
                away_pressure=34,
                home_shots_on=randint(2, 6),
                away_shots_on=randint(0, 2),
                kickoff_at=None,
                status="live",
                state="in",
                odds_home=1.75 + random() / 4,
                odds_draw=3.1,
                odds_away=4.8,
            ),
            LiveGame(
                game_id="mock-int-mil",
                league="Italy - Serie Demo",
                home="Inter Demo",
                away="Milan Demo",
                minute=randint(35, 78),
                home_goals=1,
                away_goals=1,
                home_pressure=45,
                away_pressure=51,
                home_shots_on=2,
                away_shots_on=3,
                kickoff_at=None,
                status="live",
                state="in",
                odds_home=2.2,
                odds_draw=2.8,
                odds_away=3.0,
            ),
        ]
