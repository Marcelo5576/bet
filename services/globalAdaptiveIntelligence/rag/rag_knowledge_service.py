from __future__ import annotations

from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill

from ..memory.long_term_memory_service import LongTermMemoryService


class GlobalRagKnowledgeService:
    def __init__(self, memory: LongTermMemoryService):
        self.memory = memory
        self.football_skill = get_football_quant_ai_skill()

    def searchRelevantContext(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        local = self.memory.search(query, limit=limit)
        rag = self.football_skill.rag.searchRelevantContext(query, limit=max(1, limit // 2))
        return local + rag

    def answerWithContext(self, question: str) -> dict[str, Any]:
        contexts = self.searchRelevantContext(question, limit=6)
        if not contexts:
            return {"answer": "Ainda não há contexto suficiente indexado no histórico global.", "contexts": []}
        return {
            "answer": "Encontrei contexto em memória longa, relatórios e chunks do módulo de futebol. Use como apoio estatístico, não como promessa de resultado.",
            "contexts": contexts,
        }

