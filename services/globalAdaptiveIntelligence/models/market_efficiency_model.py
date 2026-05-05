from __future__ import annotations

from typing import Any


class MarketEfficiencyModel:
    name = "marketEfficiencyModel"

    def predict(self, context: dict[str, Any], market: str, offered_odd: float | None) -> dict[str, Any]:
        implied = 0.0
        if offered_odd and offered_odd > 1:
            implied = 1 / offered_odd
        baseline = context["poisson_prediction"]
        fallback = float(baseline.get("estimated_probability", 0.5) or 0.5)
        probability = (fallback * 0.7) + (implied * 0.3 if implied else 0.0)
        confidence = 66.0 if implied else 48.0
        return {
            "model_name": self.name,
            "estimated_probability": round(min(0.95, max(0.05, probability)), 4),
            "confidence_score": confidence,
            "input_features": {"offered_odd": offered_odd, "implied_probability": round(implied, 4) if implied else None},
            "explanation": "Modelo de eficiência de mercado ponderando probabilidade implícita e baseline estatístico.",
        }

