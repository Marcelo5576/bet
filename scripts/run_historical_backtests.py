from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.footballQuantAiSkill import get_football_quant_ai_skill
from services.footballQuantAiSkill.schemas import BacktestRequest


DEFAULT_MARKETS = ["match_winner_home", "over_2_5", "btts_yes"]


def _rows(db_file: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _candidate_leagues(db_file: str, *, limit: int, min_trainable: int) -> list[str]:
    rows = _rows(
        db_file,
        """
        SELECT league, COUNT(*) AS total,
               SUM(CASE WHEN usable_for_training = 1 THEN 1 ELSE 0 END) AS trainable,
               AVG(data_quality_score) AS avg_quality
        FROM historical_matches
        WHERE league IS NOT NULL AND TRIM(league) <> ''
        GROUP BY league
        HAVING trainable >= ?
        ORDER BY trainable DESC, avg_quality DESC, total DESC
        LIMIT ?
        """,
        (max(0, int(min_trainable)), max(1, int(limit))),
    )
    return [str(row["league"]) for row in rows]


def _snapshot_counts(db_file: str) -> dict[str, int]:
    tables = [
        "historical_matches",
        "historical_odds",
        "historical_features",
        "learning_events",
        "simulation_runs",
        "simulation_results",
        "league_reliability_scores",
    ]
    counts: dict[str, int] = {}
    with sqlite3.connect(db_file) as conn:
        for table in tables:
            try:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
            except sqlite3.Error:
                counts[table] = 0
        try:
            counts["trainable_matches"] = int(
                conn.execute("SELECT COUNT(*) FROM historical_matches WHERE usable_for_training = 1").fetchone()[0] or 0
            )
        except sqlite3.Error:
            counts["trainable_matches"] = 0
    return counts


def run(args: argparse.Namespace) -> dict[str, Any]:
    skill = get_football_quant_ai_skill()
    markets = [item.strip() for item in str(args.markets or "").split(",") if item.strip()] or DEFAULT_MARKETS
    leagues = [item.strip() for item in str(args.leagues or "").split(",") if item.strip()]
    if not leagues and args.per_league:
        leagues = _candidate_leagues(
            skill.settings.db_file,
            limit=args.max_leagues,
            min_trainable=args.min_trainable_per_league,
        )

    before = _snapshot_counts(skill.settings.db_file)
    runs: list[dict[str, Any]] = []

    scopes: list[str | None] = [None]
    if args.per_league:
        scopes.extend(leagues)

    for league in scopes:
        for market in markets:
            try:
                summary = skill.backtesting.runBacktest(
                    BacktestRequest(
                        league=league,
                        season=args.season,
                        market=market,
                        ev_min=args.ev_min,
                        confidence_min=args.confidence_min,
                        bankroll=args.bankroll,
                        bankroll_profile=args.profile,
                        model_version=args.model_version,
                    )
                )
                runs.append(
                    {
                        "ok": True,
                        "league": league or "GLOBAL",
                        "market": market,
                        "simulation_run_id": summary.simulation_run_id,
                        "total_games": summary.total_games,
                        "total_entries": summary.total_entries,
                        "hit_rate": summary.hit_rate,
                        "roi": summary.roi,
                        "profit_loss": summary.profit_loss,
                    }
                )
            except Exception as exc:
                runs.append(
                    {
                        "ok": False,
                        "league": league or "GLOBAL",
                        "market": market,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )

    after = _snapshot_counts(skill.settings.db_file)
    skill.repository.log(
        "historicalBacktestRunner",
        "Backtests historicos executados.",
        payload={
            "markets": markets,
            "leagues": leagues,
            "season": args.season,
            "runs": runs,
            "before": before,
            "after": after,
        },
    )
    return {
        "ok": True,
        "markets": markets,
        "leagues": leagues,
        "runs": runs,
        "before": before,
        "after": after,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa e salva backtests históricos por mercado/liga.")
    parser.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    parser.add_argument("--leagues", default="", help="Lista de ligas separadas por vírgula. Vazio usa ranking por dados.")
    parser.add_argument("--max-leagues", type=int, default=8)
    parser.add_argument("--min-trainable-per-league", type=int, default=10)
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--ev-min", type=float, default=0.05)
    parser.add_argument("--confidence-min", type=float, default=65.0)
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--profile", default="moderado")
    parser.add_argument("--model-version", default="baseline")
    parser.add_argument("--per-league", action="store_true")
    return parser.parse_args()


def main() -> None:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
