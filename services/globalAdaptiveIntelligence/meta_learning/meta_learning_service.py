from __future__ import annotations

from typing import Any

from ..repository import GlobalAdaptiveRepository


class MetaLearningService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository

    def select_model(self, *, sport_or_market: str, league: str, market: str, data_quality: float, model_outputs: list[dict[str, Any]]) -> dict[str, Any]:
        ranked = sorted(model_outputs, key=lambda item: (float(item["confidence_score"]), float(item["estimated_probability"])), reverse=True)
        selected = ranked[0] if ranked else {"model_name": "fallback", "estimated_probability": 0.5, "confidence_score": 50.0}
        trust = min(0.99, max(0.1, (float(selected.get("confidence_score", 50.0)) / 100) * max(0.35, min(1.0, data_quality / 100))))
        reason = (
            f"Selecionado {selected['model_name']} para {sport_or_market}/{market} em {league}, "
            f"com qualidade {data_quality:.1f} e confiança {float(selected.get('confidence_score', 0)):.1f}."
        )
        decision = {
            "selected_model": selected["model_name"],
            "trust_score": round(trust, 4),
            "reason": reason,
            "decision": "use_model" if trust >= 0.45 else "defer_to_ensemble",
            "league": league,
            "market": market,
        }
        self.repository.save_meta_model_decision(
            {
                "event_id": f"{sport_or_market}:{league}:{market}",
                "market": market,
                **decision,
            }
        )
        return decision

