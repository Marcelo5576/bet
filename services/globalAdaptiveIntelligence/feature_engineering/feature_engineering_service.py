from __future__ import annotations

from typing import Any

from ..repository import GlobalAdaptiveRepository
from ..sports.football_adapter import FootballAdapter


class FeatureEngineeringService:
    def __init__(self, repository: GlobalAdaptiveRepository, football: FootballAdapter):
        self.repository = repository
        self.football = football

    def generate_for_recent_matches(self, limit: int = 40, *, user_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.football.getHistoricalEvents(limit=limit)
        features: list[dict[str, Any]] = []
        for row in rows:
            stats = self.football.getStats(int(row["id"])) or {}
            home_goals = int(row.get("home_goals") or 0)
            away_goals = int(row.get("away_goals") or 0)
            opening_home = _safe_float((row.get("odds") or [{}])[0].get("home_odd") if isinstance(row.get("odds"), list) and row.get("odds") else None)
            feature = {
                "sport_or_market": "football",
                "feature_name": f"rolling::{row['league']}",
                "scope": str(row.get("league") or "global"),
                "event_id": int(row["id"]),
                "goal_diff": home_goals - away_goals,
                "total_goals": home_goals + away_goals,
                "shots_on_balance": int(stats.get("shots_on_home") or 0) - int(stats.get("shots_on_away") or 0),
                "corners_total": int(stats.get("corners_home") or 0) + int(stats.get("corners_away") or 0),
                "cards_total": int(stats.get("yellow_home") or 0) + int(stats.get("yellow_away") or 0) + 2 * (int(stats.get("red_home") or 0) + int(stats.get("red_away") or 0)),
                "momentum_proxy": (int(stats.get("dangerous_attacks_home") or 0) - int(stats.get("dangerous_attacks_away") or 0)) + (home_goals - away_goals) * 6,
                "opening_home_odd": opening_home,
            }
            features.append(feature)
        if features:
            self.repository.save_generated_features(features, user_id=user_id)
        return features


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

