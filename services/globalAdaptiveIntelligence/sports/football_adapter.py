from __future__ import annotations

from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill
from services.footballQuantAiSkill.schemas import BacktestRequest


class FootballAdapter:
    sport_name = "football"

    def __init__(self):
        self.skill = get_football_quant_ai_skill()

    def getEvents(self, **kwargs) -> list[dict[str, Any]]:
        return self.skill.repository.list_historical_matches(limit=int(kwargs.get("limit", 30)))

    def getHistoricalEvents(self, **kwargs) -> list[dict[str, Any]]:
        return self.skill.repository.list_historical_matches(
            league=kwargs.get("league"),
            season=kwargs.get("season"),
            limit=int(kwargs.get("limit", 200)),
            offset=int(kwargs.get("offset", 0)),
        )

    def getOdds(self, **kwargs) -> list[dict[str, Any]]:
        match_id = kwargs.get("event_id")
        if not match_id:
            return []
        match = self.skill.repository.get_historical_match(int(match_id))
        return list(match.get("odds") or []) if match else []

    def getStats(self, event_id: int) -> dict[str, Any] | None:
        match = self.skill.repository.get_historical_match(int(event_id))
        return dict(match.get("stats") or {}) if match else None

    def normalizeEvent(self, payload: dict[str, Any]) -> dict[str, Any]:
        from services.footballQuantAiSkill.normalization.normalizer import FootballDataNormalizer

        item = FootballDataNormalizer().normalize_match(payload, source=str(payload.get("source") or "manual"))
        return {
            "external_id": item.external_id,
            "league": item.league,
            "country": item.country,
            "season": item.season,
            "match_date": item.match_date.isoformat(),
            "home_team": item.home_team,
            "away_team": item.away_team,
            "status": item.status,
            "source": item.source,
        }

    def runPrediction(self, event_id: int, **kwargs) -> dict[str, Any]:
        market = kwargs.get("market", "match_winner_home")
        offered_odd = kwargs.get("offered_odd")
        bankroll = kwargs.get("bankroll")
        bankroll_profile = kwargs.get("bankroll_profile")
        prediction = self.skill.prediction.predict_match(
            int(event_id),
            market=market,
            offered_odd=offered_odd,
            bankroll=bankroll,
            bankroll_profile=bankroll_profile,
            model_version=str(kwargs.get("model_version", "baseline")),
        )
        return prediction.__dict__

    def runBacktest(self, **kwargs) -> dict[str, Any]:
        summary = self.skill.backtesting.runBacktest(
            BacktestRequest(
                league=kwargs.get("league"),
                season=kwargs.get("season"),
                market=kwargs.get("market", "match_winner_home"),
                ev_min=float(kwargs.get("ev_min", 0.03)),
                confidence_min=float(kwargs.get("confidence_min", 60)),
                date_from=kwargs.get("date_from"),
                date_to=kwargs.get("date_to"),
                bankroll=float(kwargs.get("bankroll", 1000)),
                bankroll_profile=str(kwargs.get("bankroll_profile", "moderado")),
                model_version=str(kwargs.get("model_version", "baseline")),
                user_id=kwargs.get("user_id"),
            )
        )
        return summary.__dict__

