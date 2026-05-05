from __future__ import annotations

from typing import Any


class AlertService:
    def build_alerts(self, *, drift: dict[str, Any], risk: dict[str, Any], anomaly: dict[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        if drift.get("severity") in {"medium", "high"}:
            alerts.append({"type": "drift", "severity": drift.get("severity"), "message": f"Drift detectado: score {drift.get('score')}."})
        if float(risk.get("risk_score", 0.0) or 0.0) >= 65:
            alerts.append({"type": "risk", "severity": "high", "message": f"Risco alto: {risk.get('risk_score')}."})
        if anomaly.get("severity") in {"medium", "high"}:
            alerts.append({"type": "anomaly", "severity": anomaly.get("severity"), "message": anomaly.get("reason")})
        return alerts

