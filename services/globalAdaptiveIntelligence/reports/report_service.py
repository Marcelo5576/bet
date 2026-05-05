from __future__ import annotations

from typing import Any


class ReportService:
    def build_summary(self, *, audit: dict[str, Any], snapshot: dict[str, Any], control_center: dict[str, Any]) -> dict[str, Any]:
        return {
            "audit": audit,
            "snapshot": snapshot,
            "control_center": control_center,
            "generated_at": control_center.get("generated_at"),
        }

