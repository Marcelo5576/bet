from __future__ import annotations

from typing import Any


class MomentumModel:
    name = "momentumModel"

    def predict(self, context: dict[str, Any], market: str) -> dict[str, Any]:
        home = context["home_context"]
        away = context["away_context"]
        attack_gap = (home.get("goals_for_avg_5", 0) - away.get("goals_against_avg_5", 0)) - (
            away.get("goals_for_avg_5", 0) - home.get("goals_against_avg_5", 0)
        )
        probability = min(0.92, max(0.08, 0.5 + attack_gap * 0.08))
        if market.startswith("match_winner_away"):
            probability = 1 - probability
        confidence = min(90.0, 44.0 + abs(attack_gap) * 18 + (home.get("sample_size", 0) + away.get("sample_size", 0)) * 2)
        return {
            "model_name": self.name,
            "estimated_probability": round(probability, 4),
            "confidence_score": round(confidence, 2),
            "input_features": {"attack_gap": round(attack_gap, 4), "market": market},
            "explanation": "Modelo de momentum usando força ofensiva e vulnerabilidade defensiva recente.",
        }

