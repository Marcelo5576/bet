from __future__ import annotations

from typing import Any

from ..repository import GlobalAdaptiveRepository


class EnsemblePredictionService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository

    def combine(self, model_outputs: list[dict[str, Any]], *, config: dict[str, Any] | None = None) -> dict[str, Any]:
        weights = dict(config.get("weights", {})) if config else {}
        total_weight = 0.0
        probability_sum = 0.0
        confidence_sum = 0.0
        explanations: list[dict[str, Any]] = []
        for item in model_outputs:
            name = str(item["model_name"])
            weight = float(weights.get(name, 1.0))
            total_weight += weight
            probability_sum += float(item["estimated_probability"]) * weight
            confidence_sum += float(item["confidence_score"]) * weight
            explanations.append(
                {
                    "model_name": name,
                    "weight": weight,
                    "estimated_probability": item["estimated_probability"],
                    "confidence_score": item["confidence_score"],
                    "explanation": item["explanation"],
                }
            )
        divisor = total_weight or max(1.0, float(len(model_outputs)))
        probability = probability_sum / divisor
        confidence = confidence_sum / divisor
        return {
            "estimated_probability": round(probability, 4),
            "confidence_score": round(confidence, 2),
            "model_name": "ensemblePredictionService",
            "input_features": {"models": [item["model_name"] for item in model_outputs]},
            "explanation": explanations,
        }

