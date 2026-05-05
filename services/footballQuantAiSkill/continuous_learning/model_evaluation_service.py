from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class ModelEvaluationService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def current_snapshot(self) -> dict[str, Any]:
        performance = self.repository.aggregate_simulation_performance()
        suggestions = self.repository.list_strategy_suggestions(limit=20)
        return {
            "performance": performance,
            "pending_suggestions": [item for item in suggestions if item.get("status") == "pending"],
            "history": suggestions,
        }

