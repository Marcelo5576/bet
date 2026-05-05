from __future__ import annotations

from typing import Any

from ..repository import GlobalAdaptiveRepository


class LongTermMemoryService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository

    def remember(self, memory_type: str, title: str, body: str, *, payload: dict[str, Any] | None = None, user_id: int | None = None) -> int:
        return self.repository.save_long_term_memory(
            {
                "memory_type": memory_type,
                "title": title,
                "body": body,
                "search_text": f"{title} {body}".lower(),
                "payload": payload or {},
            },
            user_id=user_id,
        )

    def search(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        return self.repository.search_long_term_memory(query, limit=limit)

