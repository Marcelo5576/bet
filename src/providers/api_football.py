from __future__ import annotations

from services.footballQuantAiSkill.data_sources.api_football_provider import (
    get_shared_api_football_provider,
)
from src.usage_metrics import UsageTracker

from .base import LiveGame, LiveProvider


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class ApiFootballProvider(LiveProvider):
    label = "API-Football"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
        *,
        max_rpm: int = 20,
        cooldown_seconds: int = 60,
    ):
        self.backend = get_shared_api_football_provider(
            api_key,
            base_url,
            max_rpm=max_rpm,
            cooldown_seconds=cooldown_seconds,
            usage_tracker=usage_tracker,
            cost_per_request_brl=cost_per_request_brl,
        )

    async def get_live_games(self) -> list[LiveGame]:
        fixtures = await self.backend.get_live_fixtures()
        return [self._to_live_game(item) for item in fixtures]

    async def get_today_games(self) -> list[LiveGame]:
        from datetime import datetime, timezone

        fixtures = await self.backend.get_fixtures_by_date(datetime.now(timezone.utc).date())
        return [self._to_live_game(item) for item in fixtures]

    def status_snapshot(self) -> dict:
        return self.backend.status_snapshot()

    def _to_live_game(self, payload: dict) -> LiveGame:
        odds = payload.get("odds") or {}
        summary = odds.get("summary") or {}
        stats = payload.get("stats") or {}
        home_stats = stats.get("home") or {}
        away_stats = stats.get("away") or {}
        return LiveGame(
            game_id=str(payload.get("fixture_id") or payload.get("game_id") or ""),
            league=str(payload.get("league") or "Unknown league"),
            home=str(payload.get("home_team") or payload.get("home") or "Home"),
            away=str(payload.get("away_team") or payload.get("away") or "Away"),
            minute=_safe_int(payload.get("minute")),
            home_goals=_safe_int(payload.get("score_home")),
            away_goals=_safe_int(payload.get("score_away")),
            home_pressure=_safe_int(home_stats.get("pressure_index") or payload.get("home_pressure")),
            away_pressure=_safe_int(away_stats.get("pressure_index") or payload.get("away_pressure")),
            home_shots_on=_safe_int(home_stats.get("shots_on") or payload.get("home_shots_on")),
            away_shots_on=_safe_int(away_stats.get("shots_on") or payload.get("away_shots_on")),
            kickoff_at=str(payload.get("kickoff_at") or "").strip() or None,
            status=str(payload.get("status") or "").strip() or None,
            state=str(payload.get("state") or "").strip() or None,
            odds_home=_safe_float(payload.get("odds_home") or summary.get("home")),
            odds_draw=_safe_float(payload.get("odds_draw") or summary.get("draw")),
            odds_away=_safe_float(payload.get("odds_away") or summary.get("away")),
            division=str(payload.get("division") or payload.get("league") or "Outras ligas"),
            markets=odds,
        )
