from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class LearningMemoryService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def record_prediction_feedback(self, prediction_id: int, outcome: str, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        self.repository.save_learning_event(
            "prediction_feedback",
            {"prediction_id": prediction_id, "outcome": outcome, **payload},
            ref_type="prediction",
            ref_id=str(prediction_id),
            user_id=user_id,
        )

