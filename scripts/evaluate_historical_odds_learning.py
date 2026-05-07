from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.footballQuantAiSkill import get_football_quant_ai_skill


MARKET_ODD_FIELDS = {
    "match_winner_home": ("match_winner", None, "home_odd"),
    "over_2_5": ("over_under", "2.5", "over_odd"),
    "btts_yes": ("btts", None, "home_odd"),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def settle(match: dict[str, Any], market: str) -> str:
    home = int(match.get("home_goals") or 0)
    away = int(match.get("away_goals") or 0)
    total = home + away
    if market == "match_winner_home":
        return "WIN" if home > away else "LOSS"
    if market == "over_2_5":
        return "WIN" if total >= 3 else "LOSS"
    if market == "btts_yes":
        return "WIN" if home > 0 and away > 0 else "LOSS"
    return "LOSS"


def odds_rows(db_file: str, market: str, limit: int) -> list[dict[str, Any]]:
    odds_market, line, odd_field = MARKET_ODD_FIELDS[market]
    where = ["o.market = ?", f"o.{odd_field} IS NOT NULL"]
    params: list[Any] = [odds_market]
    if line is not None:
        where.append("o.line = ?")
        params.append(line)
    query = f"""
        SELECT
            m.*,
            o.id AS odds_id,
            o.{odd_field} AS offered_odd,
            o.bookmaker,
            o.timestamp AS odds_timestamp
        FROM historical_odds o
        JOIN historical_matches m ON m.id = o.historical_match_id
        WHERE {' AND '.join(where)}
        ORDER BY m.match_date DESC
        LIMIT ?
    """
    params.append(max(1, int(limit)))
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def run(*, markets: list[str], limit: int, ev_min: float, confidence_min: float, replace: bool) -> dict[str, Any]:
    skill = get_football_quant_ai_skill()
    repository = skill.repository
    if replace:
        with repository.connect() as conn:
            conn.execute("DELETE FROM learning_events WHERE event_type = 'historical_odds_ev'")

    summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "analyzed": 0,
            "entries": 0,
            "wins": 0,
            "losses": 0,
            "staked": 0.0,
            "profit": 0.0,
            "ev_sum": 0.0,
            "confidence_sum": 0.0,
        }
    )
    saved = 0
    for market in markets:
        if market not in MARKET_ODD_FIELDS:
            continue
        for match in odds_rows(skill.settings.db_file, market, limit):
            offered_odd = float(match.get("offered_odd") or 0)
            if offered_odd <= 1:
                continue
            prediction = skill.prediction.predict_match(int(match["id"]), market=market, offered_odd=offered_odd)
            ev = float(prediction.expected_value or 0)
            confidence = float(prediction.confidence_score or 0)
            allowed = (
                prediction.recommendation in {"ENTRA_FORTE", "ENTRA_LEVE"}
                and ev >= ev_min
                and confidence >= confidence_min
                and 1.6 <= offered_odd <= 3.0
            )
            outcome = settle(match, market)
            stake = float(prediction.bankroll.suggested_stake or 0) if allowed else 0.0
            profit = 0.0
            if allowed and stake > 0:
                if outcome == "WIN":
                    profit = round(stake * (offered_odd - 1), 2)
                else:
                    profit = round(-stake, 2)
                summary[market]["entries"] += 1
                summary[market]["wins"] += 1 if outcome == "WIN" else 0
                summary[market]["losses"] += 1 if outcome == "LOSS" else 0
                summary[market]["staked"] += stake
                summary[market]["profit"] += profit
            summary[market]["analyzed"] += 1
            summary[market]["ev_sum"] += ev
            summary[market]["confidence_sum"] += confidence
            payload = {
                "source": "historical_api_football_odds",
                "evaluated_at": _now_iso(),
                "historical_match_id": int(match["id"]),
                "external_id": match.get("external_id"),
                "league": match.get("league"),
                "season": match.get("season"),
                "match_date": match.get("match_date"),
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "score": f"{match.get('home_goals')}x{match.get('away_goals')}",
                "market": market,
                "offered_odd": offered_odd,
                "estimated_probability": prediction.estimated_probability,
                "implied_probability": round(1 / offered_odd, 4),
                "expected_value": prediction.expected_value,
                "confidence_score": prediction.confidence_score,
                "recommendation": prediction.recommendation,
                "entry_allowed": bool(allowed),
                "stake_paper": round(stake, 2),
                "result": outcome if allowed else "SKIPPED",
                "profit_paper": profit,
                "note": "Avaliacao paper com odd historica real. Nao executa aposta real.",
            }
            repository.save_learning_event(
                "historical_odds_ev",
                payload,
                ref_type="historical_match_odds",
                ref_id=f"{market}:{match['id']}",
            )
            saved += 1

    output: dict[str, Any] = {}
    for market, row in summary.items():
        analyzed = int(row["analyzed"])
        entries = int(row["entries"])
        staked = float(row["staked"])
        output[market] = {
            "analyzed": analyzed,
            "entries": entries,
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
            "hit_rate": round((row["wins"] / entries) * 100, 2) if entries else 0.0,
            "profit_paper": round(row["profit"], 2),
            "roi_on_staked": round((row["profit"] / staked) * 100, 2) if staked else 0.0,
            "avg_ev": round(row["ev_sum"] / analyzed, 4) if analyzed else 0.0,
            "avg_confidence": round(row["confidence_sum"] / analyzed, 2) if analyzed else 0.0,
        }
    repository.log(
        "historicalOddsEvaluation",
        "Avaliacao de EV com odds historicas concluida.",
        payload={"markets": markets, "saved_events": saved, "summary": output, "ev_min": ev_min, "confidence_min": confidence_min},
    )
    return {"ok": True, "saved_learning_events": saved, "summary": output, "snapshot": repository.system_snapshot()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia EV/ROI paper com odds historicas reais.")
    parser.add_argument("--markets", default="match_winner_home,over_2_5,btts_yes")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--ev-min", type=float, default=0.05)
    parser.add_argument("--confidence-min", type=float, default=65.0)
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markets = [item.strip() for item in str(args.markets or "").split(",") if item.strip()]
    result = run(markets=markets, limit=args.limit, ev_min=args.ev_min, confidence_min=args.confidence_min, replace=bool(args.replace))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
