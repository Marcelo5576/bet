from __future__ import annotations

from typing import Any


class MeanReversionModel:
    name = "meanReversionModel"

    def predict(self, context: dict[str, Any], market: str) -> dict[str, Any]:
        baseline = context["league_baseline"]
        home = context["home_context"]
        away = context["away_context"]
        total_goals_avg = float(baseline.get("total_goals_avg", 2.4) or 2.4)
        pressure = ((home.get("goals_for_avg_10", 0) + away.get("goals_for_avg_10", 0)) / 2) - total_goals_avg
        probability = min(0.88, max(0.12, 0.5 + pressure * 0.16))
        if market == "under_2_5":
            probability = 1 - probability
        confidence = min(84.0, 52.0 + abs(pressure) * 24)
        return {
            "model_name": self.name,
            "estimated_probability": round(probability, 4),
            "confidence_score": round(confidence, 2),
            "input_features": {"league_total_goals_avg": total_goals_avg, "pressure": round(pressure, 4)},
            "explanation": "Modelo de reversão à média contra a linha histórica da liga.",
        }

