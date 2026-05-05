from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from ..repository import FootballResearchRepository
from ..schemas import TeamContext


class FootballStatsService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def team_context(self, team_name: str, league: str, before_date: str | datetime | None = None, limit: int = 10) -> TeamContext:
        matches = self._recent_team_matches(team_name, league, before_date, limit)
        if not matches:
            return TeamContext(team=team_name, league=league)
        last5 = matches[:5]
        last10 = matches[:10]
        points_5 = [self._points(item, team_name) for item in last5]
        points_10 = [self._points(item, team_name) for item in last10]
        goals_for_5 = [self._team_goals(item, team_name)[0] for item in last5]
        goals_against_5 = [self._team_goals(item, team_name)[1] for item in last5]
        goals_for_10 = [self._team_goals(item, team_name)[0] for item in last10]
        goals_against_10 = [self._team_goals(item, team_name)[1] for item in last10]
        return TeamContext(
            team=team_name,
            league=league,
            sample_size=len(last5),
            form_5=round(sum(points_5) / max(1, len(points_5)), 2),
            form_10=round(sum(points_10) / max(1, len(points_10)), 2),
            goals_for_avg_5=round(sum(goals_for_5) / max(1, len(goals_for_5)), 2),
            goals_against_avg_5=round(sum(goals_against_5) / max(1, len(goals_against_5)), 2),
            goals_for_avg_10=round(sum(goals_for_10) / max(1, len(goals_for_10)), 2),
            goals_against_avg_10=round(sum(goals_against_10) / max(1, len(goals_against_10)), 2),
            over_15_rate=_rate(last5, lambda row: sum(self._team_goals(row, team_name)) >= 2),
            over_25_rate=_rate(last5, lambda row: sum(self._team_goals(row, team_name)) >= 3),
            btts_rate=_rate(last5, lambda row: self._team_goals(row, team_name)[0] > 0 and self._team_goals(row, team_name)[1] > 0),
            clean_sheet_rate=_rate(last5, lambda row: self._team_goals(row, team_name)[1] == 0),
            failed_to_score_rate=_rate(last5, lambda row: self._team_goals(row, team_name)[0] == 0),
            corner_avg=round(sum(self._corner_total(row, team_name) for row in last5) / max(1, len(last5)), 2),
            card_avg=round(sum(self._card_total(row, team_name) for row in last5) / max(1, len(last5)), 2),
            home_away_bias=round(self._home_away_bias(last10, team_name), 2),
        )

    def league_baseline(self, league: str, season: int | None = None) -> dict[str, float]:
        matches = self.repository.list_historical_matches(league=league, season=season, limit=500)
        if not matches:
            return {"home_goals_avg": 1.35, "away_goals_avg": 1.05, "total_goals_avg": 2.4}
        home_goals = [float(item.get("home_goals") or 0) for item in matches]
        away_goals = [float(item.get("away_goals") or 0) for item in matches]
        total = [h + a for h, a in zip(home_goals, away_goals)]
        return {
            "home_goals_avg": round(sum(home_goals) / max(1, len(home_goals)), 3),
            "away_goals_avg": round(sum(away_goals) / max(1, len(away_goals)), 3),
            "total_goals_avg": round(sum(total) / max(1, len(total)), 3),
        }

    def performance_clusters(self) -> dict[str, list[dict[str, Any]]]:
        perf = self.repository.aggregate_simulation_performance()
        return perf

    def _recent_team_matches(self, team_name: str, league: str, before_date: str | datetime | None, limit: int) -> list[dict[str, Any]]:
        rows = self.repository.list_historical_matches(league=league, limit=500)
        if before_date:
            cutoff = before_date.isoformat() if isinstance(before_date, datetime) else str(before_date)
            rows = [row for row in rows if str(row.get("match_date") or "") < cutoff]
        filtered = [
            row
            for row in rows
            if str(row.get("home_team") or "").lower() == team_name.lower()
            or str(row.get("away_team") or "").lower() == team_name.lower()
        ]
        filtered.sort(key=lambda row: str(row.get("match_date") or ""), reverse=True)
        return filtered[:limit]

    @staticmethod
    def _team_goals(row: dict[str, Any], team_name: str) -> tuple[int, int]:
        home = str(row.get("home_team") or "").lower() == team_name.lower()
        if home:
            return int(row.get("home_goals") or 0), int(row.get("away_goals") or 0)
        return int(row.get("away_goals") or 0), int(row.get("home_goals") or 0)

    @staticmethod
    def _points(row: dict[str, Any], team_name: str) -> int:
        goals_for, goals_against = FootballStatsService._team_goals(row, team_name)
        if goals_for > goals_against:
            return 3
        if goals_for == goals_against:
            return 1
        return 0

    def _corner_total(self, row: dict[str, Any], team_name: str) -> int:
        stats = self.repository.get_historical_match(int(row["id"])) or {}
        stat_row = stats.get("stats") or {}
        if str(row.get("home_team") or "").lower() == team_name.lower():
            return int(stat_row.get("corners_home") or 0)
        return int(stat_row.get("corners_away") or 0)

    def _card_total(self, row: dict[str, Any], team_name: str) -> int:
        stats = self.repository.get_historical_match(int(row["id"])) or {}
        stat_row = stats.get("stats") or {}
        if str(row.get("home_team") or "").lower() == team_name.lower():
            return int(stat_row.get("yellow_home") or 0) + int(stat_row.get("red_home") or 0) * 2
        return int(stat_row.get("yellow_away") or 0) + int(stat_row.get("red_away") or 0) * 2

    @staticmethod
    def _home_away_bias(rows: list[dict[str, Any]], team_name: str) -> float:
        if not rows:
            return 0.0
        score = 0.0
        for row in rows:
            home = str(row.get("home_team") or "").lower() == team_name.lower()
            points = FootballStatsService._points(row, team_name)
            score += points if home else -points * 0.6
        return score / max(1, len(rows))


def _rate(rows: list[dict[str, Any]], predicate) -> float:
    if not rows:
        return 0.0
    return round((sum(1 for row in rows if predicate(row)) / len(rows)) * 100, 2)

