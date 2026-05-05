from __future__ import annotations

from typing import Any

from .learning_memory_service import LearningMemoryService
from .model_evaluation_service import ModelEvaluationService
from .recommendation_refinement_service import RecommendationRefinementService
from .strategy_rule_service import StrategyRuleService


class ContinuousLearningService:
    def __init__(
        self,
        memory: LearningMemoryService,
        rules: StrategyRuleService,
        refinement: RecommendationRefinementService,
        evaluation: ModelEvaluationService,
    ):
        self.memory = memory
        self.rules = rules
        self.refinement = refinement
        self.evaluation = evaluation

    def evaluate_and_suggest(self, *, user_id: int | None = None) -> dict[str, Any]:
        created = self.refinement.generate_suggestions(user_id=user_id)
        return {
            "created_suggestions": created,
            "snapshot": self.evaluation.current_snapshot(),
        }

