from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..bankroll.bankroll_service import BankrollService
from ..repository import FootballResearchRepository
from ..schemas import MatchPrediction
from ..statistics.football_stats_service import FootballStatsService
from ..value_betting.value_bet_service import ValueBetService
from .poisson_model_service import PoissonModelService


class HybridPredictionService:
    def __init__(
        self,
        repository: FootballResearchRepository,
        stats_service: FootballStatsService,
        poisson_service: PoissonModelService,
        value_service: ValueBetService,
        bankroll_service: BankrollService,
        *,
        default_bankroll: float,
        default_profile: str,
        min_ev_to_recommend: float,
        min_confidence_to_recommend: float,
    ):
        self.repository = repository
        self.stats_service = stats_service
        self.poisson_service = poisson_service
        self.value_service = value_service
        self.bankroll_service = bankroll_service
        self.default_bankroll = default_bankroll
        self.default_profile = default_profile
        self.min_ev_to_recommend = min_ev_to_recommend
        self.min_confidence_to_recommend = min_confidence_to_recommend

    def predict_match(
        self,
        historical_match_id: int,
        *,
        market: str = "match_winner_home",
        offered_odd: float | None = None,
        bankroll: float | None = None,
        bankroll_profile: str | None = None,
        model_version: str = "baseline",
    ) -> MatchPrediction:
        match = self.repository.get_historical_match(historical_match_id)
        if not match:
            raise ValueError(f"Partida histórica {historical_match_id} não encontrada.")
        home_ctx = self.stats_service.team_context(str(match["home_team"]), str(match["league"]), match["match_date"])
        away_ctx = self.stats_service.team_context(str(match["away_team"]), str(match["league"]), match["match_date"])
        baseline = self.stats_service.league_baseline(str(match["league"]), match.get("season"))
        poisson = self.poisson_service.predict(home_ctx, away_ctx, baseline)
        estimated_probability = self._market_probability(poisson, market)
        confidence_score = self._confidence_score(home_ctx, away_ctx, poisson, market)
        risk_level = self._risk_level(home_ctx.sample_size, confidence_score, match.get("status"))
        resolved_odd = offered_odd or self._best_offered_odd(match, market)
        value = self.value_service.assess(estimated_probability, resolved_odd, confidence_score)
        recommendation = self._recommendation(value, confidence_score, risk_level)
        bankroll_advice = self.bankroll_service.recommend(
            bankroll or self.default_bankroll,
            estimated_probability,
            resolved_odd,
            bankroll_profile or self.default_profile,
        )
        explanation = {
            "home_context": home_ctx.__dict__,
            "away_context": away_ctx.__dict__,
            "league_baseline": baseline,
            "poisson": poisson.__dict__,
            "value_assessment": value.__dict__,
            "legal_notice": "Este sistema é apenas uma ferramenta estatística de apoio. Não garante lucro. Aposte com responsabilidade.",
        }
        return MatchPrediction(
            match_id=historical_match_id,
            market=market,
            recommendation=recommendation,
            confidence_score=confidence_score,
            risk_level=risk_level,
            estimated_probability=round(estimated_probability, 4),
            fair_odd=value.fair_odd,
            offered_odd=resolved_odd,
            expected_value=value.expected_value,
            value_band=value.band,
            explanation=explanation,
            bankroll=bankroll_advice,
            model_version=model_version,
            created_at=datetime.now(timezone.utc),
        )

    def _market_probability(self, poisson, market: str) -> float:
        if market == "match_winner_home":
            return poisson.home_win
        if market == "match_winner_draw":
            return poisson.draw
        if market == "match_winner_away":
            return poisson.away_win
        if market == "over_2_5":
            return poisson.over_25
        if market == "under_2_5":
            return poisson.under_25
        if market == "btts_yes":
            return poisson.btts_yes
        if market == "btts_no":
            return poisson.btts_no
        return poisson.home_win

    def _confidence_score(self, home_ctx, away_ctx, poisson, market: str) -> float:
        sample_factor = min(18.0, (home_ctx.sample_size + away_ctx.sample_size) * 1.6)
        form_gap = abs(home_ctx.form_5 - away_ctx.form_5)
        probability = self._market_probability(poisson, market)
        base = probability * 100
        confidence = base + sample_factor + min(8.0, form_gap * 2)
        if market.startswith("over_") or market.startswith("under_"):
            confidence += min(8.0, abs(home_ctx.over_25_rate - away_ctx.over_25_rate) * 0.08)
        if market.startswith("btts"):
            confidence += min(8.0, ((home_ctx.btts_rate + away_ctx.btts_rate) / 2) * 0.08)
        return round(max(35.0, min(92.0, confidence)), 2)

    @staticmethod
    def _risk_level(sample_size: int, confidence_score: float, status: str | None) -> str:
        if sample_size < 4 or confidence_score < 55:
            return "alto"
        if status and str(status).upper() not in {"FT", "AET", "PEN", "FINISHED"}:
            return "moderado"
        if confidence_score >= 70:
            return "baixo"
        return "moderado"

    def _recommendation(self, value, confidence_score: float, risk_level: str) -> str:
        if value.expected_value is None or value.expected_value <= 0:
            return "NO_BET"
        if confidence_score < self.min_confidence_to_recommend:
            return "NO_BET"
        if value.expected_value < self.min_ev_to_recommend:
            return "ESPERA"
        if risk_level == "alto":
            return "ENTRA_LEVE"
        if value.expected_value >= 0.08 and confidence_score >= 72:
            return "ENTRA_FORTE"
        return "ENTRA_LEVE"

    @staticmethod
    def _best_offered_odd(match: dict[str, Any], market: str) -> float | None:
        odds = match.get("odds") or []
        if market == "match_winner_home":
            return _first_float([row.get("home_odd") for row in odds])
        if market == "match_winner_draw":
            return _first_float([row.get("draw_odd") for row in odds])
        if market == "match_winner_away":
            return _first_float([row.get("away_odd") for row in odds])
        if market == "over_2_5":
            return _first_float([row.get("over_odd") for row in odds if str(row.get("line") or "") == "2.5"])
        if market == "under_2_5":
            return _first_float([row.get("under_odd") for row in odds if str(row.get("line") or "") == "2.5"])
        if market.startswith("btts"):
            return _first_float([row.get("home_odd") for row in odds if str(row.get("market") or "").lower() == "btts"])
        return _first_float([row.get("home_odd") for row in odds])


def _first_float(values: list[Any]) -> float | None:
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None

