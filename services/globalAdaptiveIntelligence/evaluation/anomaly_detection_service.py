from __future__ import annotations

from typing import Any


class AnomalyDetectionService:
    def detect(self, *, offered_odd: float | None, fair_odd: float | None, liquidity_hint: float | None = None) -> dict[str, Any]:
        if not offered_odd or not fair_odd:
            return {"anomaly_type": "missing_odds", "severity": "low", "score": 0.1, "reason": "Sem odd suficiente para detectar anomalia."}
        deviation = abs(offered_odd - fair_odd) / max(fair_odd, 0.01)
        liquidity_risk = 0.2 if liquidity_hint is not None and liquidity_hint < 0.4 else 0.0
        score = min(1.0, deviation + liquidity_risk)
        severity = "high" if score >= 0.8 else "medium" if score >= 0.45 else "low"
        return {"anomaly_type": "odds_drift", "severity": severity, "score": round(score, 4), "reason": f"Desvio entre odd ofertada e justa em {deviation:.3f}."}

