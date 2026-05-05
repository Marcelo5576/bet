from __future__ import annotations

from typing import Any


class GlobalRiskService:
    def evaluate(self, *, bankroll: float, stake: float, expected_value: float, confidence_score: float, drift_score: float) -> dict[str, Any]:
        exposure_pct = (stake / bankroll) if bankroll > 0 else 0.0
        ruin_risk = min(0.99, max(0.0, (exposure_pct * 5) + max(0.0, 0.5 - (confidence_score / 100)) + drift_score))
        risk_score = min(100.0, max(0.0, (exposure_pct * 100 * 1.8) + (1 - max(expected_value, 0.0)) * 25 + drift_score * 40))
        suggested_stake = min(stake, bankroll * 0.03)
        if drift_score >= 0.45 or confidence_score < 55 or expected_value <= 0:
            suggested_stake = min(suggested_stake, bankroll * 0.01)
        return {
            "risk_score": round(risk_score, 2),
            "risk_of_ruin": round(ruin_risk, 4),
            "total_exposure": round(exposure_pct * 100, 2),
            "suggested_stake": round(max(0.0, suggested_stake), 2),
            "notes": "Sem martingale; stake máxima padrão 3% e redução automática em drift alto.",
        }

