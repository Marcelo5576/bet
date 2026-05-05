from __future__ import annotations

from typing import Any

from ..repository import GlobalAdaptiveRepository


class GovernanceService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository

    def request_change(self, *, change_type: str, target_ref: str, payload: dict[str, Any], user_id: int | None = None) -> int:
        rollback_id = self.repository.create_rollback_point(
            f"{change_type}:{target_ref}",
            {"change_type": change_type, "target_ref": target_ref, "payload": payload},
            user_id=user_id,
        )
        request_id = self.repository.create_approval_request(
            {
                "change_type": change_type,
                "target_ref": target_ref,
                "payload": payload,
                "rollback_point_id": rollback_id,
            },
            user_id=user_id,
        )
        return request_id

    def decide(self, request_id: int, decision: str, *, user_id: int | None = None) -> dict[str, Any] | None:
        return self.repository.decide_approval_request(request_id, decision, user_id=user_id)

    def snapshot(self) -> dict[str, Any]:
        return {
            "pending": self.repository.list_approval_requests(status="pending", limit=30),
            "history": self.repository.list_approval_requests(limit=30),
            "rollback_points": self.repository.list_rollback_points(limit=20),
        }

