from __future__ import annotations

from typing import Any


class TimeSeriesBaselineModel:
    name = "timeSeriesBaselineModel"

    def predict(self, context: dict[str, Any], market: str) -> dict[str, Any]:
        home = context["home_context"]
        away = context["away_context"]
        probability = min(0.9, max(0.1, 0.42 + ((home.get("form_5", 0) - away.get("form_5", 0)) * 0.03)))
        if market == "over_2_5":
            probability = min(0.9, max(0.08, ((home.get("over_25_rate", 0) + away.get("over_25_rate", 0)) / 200)))
        elif market == "btts_yes":
            probability = min(0.9, max(0.08, ((home.get("btts_rate", 0) + away.get("btts_rate", 0)) / 200)))
        confidence = min(88.0, 48.0 + (home.get("sample_size", 0) + away.get("sample_size", 0)) * 3.2)
        return {
            "model_name": self.name,
            "estimated_probability": round(probability, 4),
            "confidence_score": round(confidence, 2),
            "input_features": {
                "form_5_home": home.get("form_5"),
                "form_5_away": away.get("form_5"),
                "market": market,
            },
            "explanation": "Baseline temporal usando forma recente e taxas históricas.",
        }

