from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..repository import FootballResearchRepository
from ..schemas import BacktestRequest, BacktestSummary
from ..models.hybrid_prediction_service import HybridPredictionService


class BacktestingService:
    def __init__(self, repository: FootballResearchRepository, prediction_service: HybridPredictionService):
        self.repository = repository
        self.prediction_service = prediction_service

    def runBacktest(self, payload: BacktestRequest) -> BacktestSummary:
        matches = self.repository.list_historical_matches(league=payload.league, season=payload.season, limit=2000)
        bankroll = payload.bankroll
        initial_bankroll = bankroll
        peak_bankroll = bankroll
        total_entries = 0
        wins = 0
        losses = 0
        rows: list[dict[str, Any]] = []
        for match in sorted(matches, key=lambda item: str(item.get("match_date") or "")):
            prediction = self.prediction_service.predict_match(
                int(match["id"]),
                market=payload.market,
                bankroll=bankroll,
                bankroll_profile=payload.bankroll_profile,
                model_version=payload.model_version,
            )
            entered = prediction.recommendation in {"ENTRA_FORTE", "ENTRA_LEVE"} and (prediction.expected_value or 0) >= payload.ev_min and prediction.confidence_score >= payload.confidence_min
            stake = prediction.bankroll.suggested_stake if entered else 0.0
            result = self._settle_result(match, prediction.market)
            profit_loss = 0.0
            if entered and stake > 0:
                total_entries += 1
                if result == "WIN":
                    wins += 1
                    profit_loss = round(stake * max((prediction.offered_odd or 1) - 1, 0), 2)
                elif result == "LOSS":
                    losses += 1
                    profit_loss = round(-stake, 2)
                bankroll = round(bankroll + profit_loss, 2)
                peak_bankroll = max(peak_bankroll, bankroll)
            prediction_id = self.repository.save_prediction(prediction, user_id=payload.user_id)
            rows.append(
                {
                    "historical_match_id": int(match["id"]),
                    "prediction_id": prediction_id,
                    "market": prediction.market,
                    "offered_odd": prediction.offered_odd,
                    "fair_odd": prediction.fair_odd,
                    "expected_value": prediction.expected_value,
                    "stake": stake,
                    "result": result if entered else "SKIPPED",
                    "profit_loss": profit_loss,
                    "bankroll_after": bankroll,
                    "league": match.get("league"),
                }
            )
        total_games = len(matches)
        roi = round((((bankroll - initial_bankroll) / initial_bankroll) * 100), 2) if initial_bankroll else 0.0
        hit_rate = round((wins / total_entries) * 100, 2) if total_entries else 0.0
        drawdown = round(max(0.0, peak_bankroll - bankroll), 2)
        summary_dict = {
            "label": f"Backtest {payload.market}",
            "league": payload.league,
            "market": payload.market,
            "season": payload.season,
            "date_from": payload.date_from,
            "date_to": payload.date_to,
            "initial_bankroll": initial_bankroll,
            "final_bankroll": bankroll,
            "total_games": total_games,
            "total_entries": total_entries,
            "hit_rate": hit_rate,
            "roi": roi,
            "drawdown_max": drawdown,
            "profit_loss": round(bankroll - initial_bankroll, 2),
            "status": "completed",
        }
        run_id = self.repository.save_simulation_run(summary_dict, user_id=payload.user_id)
        self.repository.save_simulation_results(run_id, rows, user_id=payload.user_id)
        grouped = self._group_rows(rows)
        return BacktestSummary(
            simulation_run_id=run_id,
            total_games=total_games,
            total_entries=total_entries,
            hit_rate=hit_rate,
            roi=roi,
            profit_loss=round(bankroll - initial_bankroll, 2),
            initial_bankroll=initial_bankroll,
            final_bankroll=bankroll,
            drawdown_max=drawdown,
            by_league=grouped["by_league"],
            by_market=grouped["by_market"],
            by_odds_range=grouped["by_odds_range"],
            by_ev_band=grouped["by_ev_band"],
        )

    def _settle_result(self, match: dict[str, Any], market: str) -> str:
        home = int(match.get("home_goals") or 0)
        away = int(match.get("away_goals") or 0)
        total = home + away
        if market == "match_winner_home":
            return "WIN" if home > away else "LOSS"
        if market == "match_winner_draw":
            return "WIN" if home == away else "LOSS"
        if market == "match_winner_away":
            return "WIN" if away > home else "LOSS"
        if market == "over_2_5":
            return "WIN" if total >= 3 else "LOSS"
        if market == "under_2_5":
            return "WIN" if total <= 2 else "LOSS"
        if market == "btts_yes":
            return "WIN" if home > 0 and away > 0 else "LOSS"
        if market == "btts_no":
            return "WIN" if home == 0 or away == 0 else "LOSS"
        return "LOSS"

    def _group_rows(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_odds_range: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_ev_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["result"] == "SKIPPED":
                continue
            by_league[str(row.get("league") or "Sem liga")].append(row)
            by_market[str(row.get("market") or "misc")].append(row)
            by_odds_range[_odds_band(float(row.get("offered_odd") or 0))].append(row)
            by_ev_band[_ev_band(float(row.get("expected_value") or 0))].append(row)
        return {
            "by_league": _summarize_group(by_league),
            "by_market": _summarize_group(by_market),
            "by_odds_range": _summarize_group(by_odds_range),
            "by_ev_band": _summarize_group(by_ev_band),
        }


def _summarize_group(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, items in groups.items():
        total = len(items)
        wins = sum(1 for item in items if item["result"] == "WIN")
        profit = round(sum(float(item["profit_loss"] or 0) for item in items), 2)
        rows.append(
            {
                "label": label,
                "total": total,
                "hit_rate": round((wins / total) * 100, 2) if total else 0.0,
                "profit_loss": profit,
            }
        )
    rows.sort(key=lambda item: item["profit_loss"], reverse=True)
    return rows


def _odds_band(odd: float) -> str:
    if odd <= 0:
        return "sem_odds"
    if odd < 1.5:
        return "1.00-1.49"
    if odd < 2.0:
        return "1.50-1.99"
    if odd < 3.0:
        return "2.00-2.99"
    return "3.00+"


def _ev_band(ev: float) -> str:
    if ev <= 0:
        return "Sem valor"
    if ev < 0.03:
        return "Baixo"
    if ev < 0.08:
        return "Moderado"
    return "Alto"

