from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class RecommendationRefinementService:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def generate_suggestions(self, *, user_id: int | None = None) -> list[int]:
        performance = self.repository.aggregate_simulation_performance()
        created: list[int] = []
        worst_leagues = [row for row in performance.get("by_league", []) if row.get("profit_loss", 0) < 0][:3]
        best_markets = [row for row in performance.get("by_market", []) if row.get("profit_loss", 0) > 0][:3]
        risky_odds = [row for row in performance.get("by_odds", []) if row.get("profit_loss", 0) < 0][:2]
        if worst_leagues:
            created.append(
                self.repository.save_strategy_suggestion(
                    suggestion_type="league_filter",
                    title="Ligas com ROI fraco",
                    description="O módulo detectou ligas com resultado negativo recorrente. Vale restringir ou aumentar o EV mínimo.",
                    payload={"worst_leagues": worst_leagues},
                    user_id=user_id,
                )
            )
        if best_markets:
            created.append(
                self.repository.save_strategy_suggestion(
                    suggestion_type="market_focus",
                    title="Mercados com melhor aderência",
                    description="Há mercados com performance acima da média. Podemos dar mais peso a eles em uma nova versão de estratégia.",
                    payload={"best_markets": best_markets},
                    user_id=user_id,
                )
            )
        if risky_odds:
            created.append(
                self.repository.save_strategy_suggestion(
                    suggestion_type="odds_guardrail",
                    title="Faixas de odds perigosas",
                    description="Algumas faixas de preço vêm consumindo ROI. Vale endurecer o filtro de odd justa.",
                    payload={"risky_odds": risky_odds},
                    user_id=user_id,
                )
            )
        return created

