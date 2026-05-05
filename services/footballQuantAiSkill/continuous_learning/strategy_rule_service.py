from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class StrategyRuleService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def create_draft(self, *, name: str, version_name: str, rules: dict[str, Any], notes: str = "", user_id: int | None = None) -> int:
        return self.repository.save_strategy_rule(
            name=name,
            version_name=version_name,
            rules=rules,
            notes=notes,
            status="draft",
            user_id=user_id,
        )

