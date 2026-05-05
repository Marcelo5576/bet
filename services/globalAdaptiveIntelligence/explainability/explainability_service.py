from __future__ import annotations

from typing import Any


class ExplainabilityService:
    def explain_prediction(self, *, prediction: dict[str, Any], consensus: dict[str, Any], meta: dict[str, Any], alerts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "recommendation": prediction.get("recommendation") or consensus.get("final_decision"),
            "estimated_probability": prediction.get("estimated_probability"),
            "expected_value": prediction.get("expected_value"),
            "confidence_score": prediction.get("confidence_score"),
            "risk_level": prediction.get("risk_level"),
            "selected_model": meta.get("selected_model"),
            "meta_reason": meta.get("reason"),
            "consensus": consensus,
            "alerts": alerts,
            "note": "Este sistema é uma ferramenta estatística de apoio. Não garante lucro. Use com responsabilidade.",
        }

