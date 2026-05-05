from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class DecisionLogStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists decision_logs (
                    analysis_id text primary key,
                    match_id text,
                    league text,
                    market text,
                    selection text,
                    odd real,
                    estimated_probability real,
                    implied_probability real,
                    expected_value real,
                    confidence_score real,
                    final_score real,
                    recommendation text,
                    risk_level text,
                    entry_allowed integer not null default 0,
                    stake_suggestion real,
                    reasons text,
                    payload text,
                    created_at text not null
                )
                """
            )
            conn.execute(
                "create index if not exists decision_logs_created_at_idx on decision_logs(created_at desc)"
            )
            conn.execute(
                "create index if not exists decision_logs_market_idx on decision_logs(market, created_at desc)"
            )
            conn.execute(
                "create index if not exists decision_logs_league_idx on decision_logs(league, created_at desc)"
            )
            conn.execute(
                """
                create table if not exists backtest_runs (
                    run_id text primary key,
                    league text,
                    market text,
                    filters text,
                    summary text,
                    rows_payload text,
                    created_at text not null
                )
                """
            )
            conn.execute(
                "create index if not exists backtest_runs_created_at_idx on backtest_runs(created_at desc)"
            )
            conn.execute(
                "create index if not exists backtest_runs_market_idx on backtest_runs(market, created_at desc)"
            )
            conn.execute(
                "create index if not exists backtest_runs_league_idx on backtest_runs(league, created_at desc)"
            )

    def log_signal(self, signal: dict[str, Any]) -> None:
        game = signal.get("game") or {}
        created_at = str(signal.get("created_at") or datetime.now(timezone.utc).isoformat())
        minute = int(_safe_float(game.get("minute")))
        analysis_id = str(
            signal.get("analysis_id")
            or signal.get("signal_id")
            or f"{game.get('game_id') or '-'}:{signal.get('market_category') or signal.get('market') or '-'}:{minute}:{created_at[:16]}"
        )
        reasons = signal.get("decision_reasons") or []
        payload = {
            "match": f"{game.get('home', '-')} x {game.get('away', '-')}",
            "market_category": signal.get("market_category"),
            "risk_profile": signal.get("effective_risk_profile") or signal.get("risk_profile"),
            "ai_explanation": signal.get("ai_explanation"),
            "decision_class": signal.get("decision_class"),
            "scan_scope": signal.get("scan_scope"),
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into decision_logs (
                    analysis_id, match_id, league, market, selection, odd,
                    estimated_probability, implied_probability, expected_value,
                    confidence_score, final_score, recommendation, risk_level,
                    entry_allowed, stake_suggestion, reasons, payload, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    str(game.get("game_id") or signal.get("match_id") or ""),
                    str(game.get("league") or game.get("division") or signal.get("league_name") or ""),
                    str(signal.get("market_category") or signal.get("entry_market") or signal.get("market") or ""),
                    str(signal.get("entry_selection") or signal.get("selection") or signal.get("team") or ""),
                    _safe_float(signal.get("entry_odds") or signal.get("target_odds"), None),
                    _safe_float(signal.get("estimated_probability"), None),
                    _safe_float(signal.get("implied_probability"), None),
                    _safe_float(signal.get("expected_value"), None),
                    _safe_float(signal.get("confidence_score"), None),
                    _safe_float(signal.get("final_score"), None),
                    str(signal.get("recommendation") or ""),
                    str(signal.get("risk_level") or ""),
                    1 if bool(signal.get("entry_allowed")) else 0,
                    _safe_float(signal.get("stake_value"), 0.0),
                    json.dumps(reasons, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )

    def save_backtest(self, result: dict[str, Any]) -> str:
        created_at = datetime.now(timezone.utc).isoformat()
        league = str(result.get("league") or "all")
        market = str(result.get("market") or "all")
        run_id = f"bt:{league}:{market}:{created_at[:19]}"
        filters = dict(result.get("filters") or {})
        summary = {
            "entries": int(_safe_float(result.get("entries"), 0) or 0),
            "greens": int(_safe_float(result.get("greens"), 0) or 0),
            "reds": int(_safe_float(result.get("reds"), 0) or 0),
            "hit_rate": _safe_float(result.get("hit_rate"), 0.0),
            "roi_units": _safe_float(result.get("roi_units"), 0.0),
            "profit_units": _safe_float(result.get("profit_units"), 0.0),
            "start_bankroll": _safe_float(result.get("start_bankroll"), 0.0),
            "end_bankroll": _safe_float(result.get("end_bankroll"), 0.0),
            "max_drawdown_units": _safe_float(result.get("max_drawdown_units"), 0.0),
        }
        rows_payload = list(result.get("rows") or [])[:120]
        with self._connect() as conn:
            conn.execute(
                """
                insert or replace into backtest_runs (
                    run_id, league, market, filters, summary, rows_payload, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    league,
                    market,
                    json.dumps(filters, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False),
                    json.dumps(rows_payload, ensure_ascii=False),
                    created_at,
                ),
            )
        return run_id

    def latest_backtests(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select run_id, league, market, filters, summary, rows_payload, created_at
                from backtest_runs
                order by created_at desc
                limit ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            payload.append(
                {
                    "run_id": row["run_id"],
                    "league": row["league"],
                    "market": row["market"],
                    "filters": _json_loads(row["filters"], {}),
                    "summary": _json_loads(row["summary"], {}),
                    "rows": _json_loads(row["rows_payload"], []),
                    "created_at": row["created_at"],
                }
            )
        return payload


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
