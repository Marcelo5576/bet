from __future__ import annotations

import math

from ..schemas import PoissonPrediction, TeamContext


class PoissonModelService:
    def predict(self, home: TeamContext, away: TeamContext, league_baseline: dict[str, float]) -> PoissonPrediction:
        home_attack_strength = self._safe_div(home.goals_for_avg_5 or home.goals_for_avg_10 or 1.1, league_baseline.get("home_goals_avg", 1.35))
        away_attack_strength = self._safe_div(away.goals_for_avg_5 or away.goals_for_avg_10 or 1.0, league_baseline.get("away_goals_avg", 1.05))
        home_defense_weakness = self._safe_div(away.goals_against_avg_5 or away.goals_against_avg_10 or 1.0, league_baseline.get("away_goals_avg", 1.05))
        away_defense_weakness = self._safe_div(home.goals_against_avg_5 or home.goals_against_avg_10 or 1.0, league_baseline.get("home_goals_avg", 1.35))
        home_lambda = max(0.2, league_baseline.get("home_goals_avg", 1.35) * home_attack_strength * home_defense_weakness)
        away_lambda = max(0.2, league_baseline.get("away_goals_avg", 1.05) * away_attack_strength * away_defense_weakness)
        matrix = [[self._poisson(i, home_lambda) * self._poisson(j, away_lambda) for j in range(7)] for i in range(7)]
        home_win = sum(matrix[i][j] for i in range(7) for j in range(7) if i > j)
        draw = sum(matrix[i][i] for i in range(7))
        away_win = sum(matrix[i][j] for i in range(7) for j in range(7) if i < j)
        over_25 = sum(matrix[i][j] for i in range(7) for j in range(7) if i + j >= 3)
        under_25 = 1 - over_25
        btts_yes = sum(matrix[i][j] for i in range(1, 7) for j in range(1, 7))
        btts_no = 1 - btts_yes
        return PoissonPrediction(
            home_lambda=round(home_lambda, 4),
            away_lambda=round(away_lambda, 4),
            home_win=round(home_win, 4),
            draw=round(draw, 4),
            away_win=round(away_win, 4),
            over_25=round(over_25, 4),
            under_25=round(under_25, 4),
            btts_yes=round(btts_yes, 4),
            btts_no=round(btts_no, 4),
            score_matrix=[[round(cell, 6) for cell in row] for row in matrix],
        )

    @staticmethod
    def _poisson(goals: int, avg: float) -> float:
        return math.exp(-avg) * (avg**goals) / math.factorial(goals)

    @staticmethod
    def _safe_div(top: float, bottom: float) -> float:
        if not bottom:
            return 1.0
        return max(0.35, min(2.35, top / bottom))

