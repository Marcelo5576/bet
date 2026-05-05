from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class RagKnowledgeService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def ingestDocument(self, title: str, body: str, *, source_type: str = "note", source_ref: str | None = None, metadata: dict[str, Any] | None = None, user_id: int | None = None) -> int:
        document_id = self.repository.save_rag_document(
            title=title,
            source_type=source_type,
            source_ref=source_ref,
            body=body,
            metadata=metadata or {},
            user_id=user_id,
        )
        chunks = self.chunkDocument(body)
        self.repository.replace_rag_chunks(document_id, chunks, user_id=user_id)
        return document_id

    def chunkDocument(self, body: str, chunk_size: int = 500) -> list[dict[str, Any]]:
        text = str(body or "").strip()
        if not text:
            return []
        chunks: list[dict[str, Any]] = []
        for idx, start in enumerate(range(0, len(text), chunk_size)):
            chunk = text[start : start + chunk_size]
            chunks.append(
                {
                    "chunk_index": idx,
                    "content": chunk,
                    "tokens_estimate": max(1, len(chunk.split())),
                    "search_text": chunk.lower(),
                    "metadata": {"length": len(chunk)},
                }
            )
        return chunks

    def searchRelevantContext(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.repository.search_rag_chunks(query, limit=limit)

    def answerWithContext(self, question: str) -> dict[str, Any]:
        contexts = self.searchRelevantContext(question, limit=4)
        if not contexts:
            return {"answer": "Ainda não há contexto suficiente indexado para responder com segurança.", "contexts": []}
        bullets = [f"{item.get('title')}: {str(item.get('content') or '')[:180]}" for item in contexts]
        return {
            "answer": "Contexto encontrado no histórico e nos relatórios internos. Use isso como apoio estatístico, não como promessa de resultado.\n- " + "\n- ".join(bullets),
            "contexts": contexts,
        }

