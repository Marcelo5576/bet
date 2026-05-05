from __future__ import annotations

from typing import Any


class DriftDetectionService:
    def detect(self, *, recent_roi: float, recent_hit_rate: float, baseline_roi: float, baseline_hit_rate: float) -> dict[str, Any]:
        roi_gap = abs(recent_roi - baseline_roi) / max(1.0, abs(baseline_roi) + 1.0)
        hit_gap = abs(recent_hit_rate - baseline_hit_rate) / max(1.0, abs(baseline_hit_rate) + 1.0)
        score = min(1.0, max(0.0, (roi_gap * 0.6) + (hit_gap * 0.4)))
        severity = "high" if score >= 0.6 else "medium" if score >= 0.35 else "low"
        return {"drift_type": "performance_drift", "score": round(score, 4), "severity": severity, "roi_gap": round(roi_gap, 4), "hit_gap": round(hit_gap, 4)}

