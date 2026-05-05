from __future__ import annotations

from typing import Any

from ..backtesting.backtesting_service import BacktestingService
from ..continuous_learning.model_evaluation_service import ModelEvaluationService
from ..rag.rag_knowledge_service import RagKnowledgeService
from ..repository import FootballResearchRepository
from ..schemas import BacktestRequest


class FootballResearchAgent:
    def __init__(
        self,
        repository: FootballResearchRepository,
        rag: RagKnowledgeService,
        evaluation: ModelEvaluationService,
        backtesting: BacktestingService,
    ):
        self.repository = repository
        self.rag = rag
        self.evaluation = evaluation
        self.backtesting = backtesting

    def answer(self, prompt: str, *, user_id: int | None = None) -> dict[str, Any]:
        text = (prompt or "").strip().lower()
        if "melhor roi" in text and "liga" in text:
            perf = self.repository.aggregate_simulation_performance()
            top = (perf.get("by_league") or [{}])[0]
            return {"answer": f"A liga com melhor ROI até agora é {top.get('name', 'indefinida')}, lucro {top.get('profit_loss', 0)}.", "data": top}
        if "mercado performou melhor" in text or "melhor mercado" in text:
            perf = self.repository.aggregate_simulation_performance()
            top = (perf.get("by_market") or [{}])[0]
            return {"answer": f"O mercado que mais performou foi {top.get('name', 'indefinido')}.", "data": top}
        if "regras est" in text and "ruim" in text:
            snapshot = self.evaluation.current_snapshot()
            pending = snapshot.get("pending_suggestions") or []
            return {"answer": "As regras mais suspeitas aparecem nas sugestões pendentes. Revise ligas negativas e faixas de odds perigosas.", "data": pending}
        if "compare estrategia" in text or "estratégia atual" in text:
            return {"answer": "A comparação de estratégia está baseada no histórico de sugestões e runs. Hoje o módulo ainda opera com baseline + drafts aprováveis.", "data": self.evaluation.current_snapshot()}
        if "rode backtest" in text or "run backtest" in text:
            league = None
            if "liga " in text:
                league = prompt.split("liga ", 1)[-1].strip()[:80]
            summary = self.backtesting.runBacktest(BacktestRequest(league=league))
            return {"answer": f"Backtest concluído. ROI {summary.roi}% em {summary.total_entries} entradas.", "data": summary.__dict__}
        return self.rag.answerWithContext(prompt)

