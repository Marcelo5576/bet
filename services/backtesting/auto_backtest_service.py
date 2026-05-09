from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    import json

    try:
        data = json.loads(value)
    except Exception:
        return default
    return data if data is not None else default


@dataclass(frozen=True)
class SettledSignal:
    signal_id: str
    league: str
    market: str
    provider: str
    created_at: str
    settled_at: str
    estimated_probability: float
    odd: float
    profit_loss: float
    apex_score: float
    decision: str


class AutoBacktestService:
    def __init__(self, db_file: str | Path):
        self.path = Path(db_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path.as_posix(), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS apex_backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS apex_backtest_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    roi REAL NOT NULL DEFAULT 0,
                    hit_rate REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    brier_score REAL NOT NULL DEFAULT 0,
                    log_loss REAL NOT NULL DEFAULT 0,
                    clv REAL NOT NULL DEFAULT 0,
                    yield REAL NOT NULL DEFAULT 0,
                    volume INTEGER NOT NULL DEFAULT 0,
                    stability REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS apex_market_performance (
                    market_key TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    volume INTEGER NOT NULL DEFAULT 0,
                    roi REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    data_quality REAL NOT NULL DEFAULT 0,
                    odds_availability REAL NOT NULL DEFAULT 0,
                    stability REAL NOT NULL DEFAULT 0,
                    classification TEXT NOT NULL DEFAULT 'neutro',
                    score REAL NOT NULL DEFAULT 50,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS apex_league_performance (
                    league_key TEXT PRIMARY KEY,
                    league TEXT NOT NULL,
                    volume INTEGER NOT NULL DEFAULT 0,
                    roi REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    data_quality REAL NOT NULL DEFAULT 0,
                    odds_availability REAL NOT NULL DEFAULT 0,
                    stability REAL NOT NULL DEFAULT 0,
                    classification TEXT NOT NULL DEFAULT 'observacao',
                    score REAL NOT NULL DEFAULT 50,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def daily_backtest_job(self) -> dict[str, Any]:
        rows = self._settled_rows()
        created_at = _now_iso()
        run_id = f"apex-bt:{created_at[:19]}:{uuid4().hex[:6]}"
        grouped_market = self._group(rows, "market")
        grouped_league = self._group(rows, "league")
        overall = self._summary(rows)
        with self.connect() as conn:
            import json

            conn.execute(
                "INSERT OR REPLACE INTO apex_backtest_runs (run_id, profile, summary_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, "auto_daily", json.dumps(overall, ensure_ascii=False), created_at),
            )
            for scope_type, groups in (("market", grouped_market), ("league", grouped_league)):
                for scope_key, items in groups.items():
                    summary = self._summary(items)
                    payload = {**summary, "scope_type": scope_type, "scope_key": scope_key}
                    result_id = f"{scope_type}:{scope_key}:{run_id}"
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO apex_backtest_results (
                            result_id, run_id, scope_type, scope_key, roi, hit_rate, drawdown,
                            brier_score, log_loss, clv, yield, volume, stability, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            result_id,
                            run_id,
                            scope_type,
                            scope_key,
                            summary["roi"],
                            summary["hit_rate"],
                            summary["drawdown"],
                            summary["brier_score"],
                            summary["log_loss"],
                            summary["clv"],
                            summary["yield"],
                            summary["volume"],
                            summary["stability"],
                            json.dumps(payload, ensure_ascii=False),
                            created_at,
                        ),
                    )
                    self._upsert_scope(conn, scope_type, scope_key, summary, created_at)
        return {"run_id": run_id, "overall": overall, "markets": len(grouped_market), "leagues": len(grouped_league)}

    def latest_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT run_id, profile, summary_json, created_at FROM apex_backtest_runs ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [{**dict(row), "summary": _loads(row["summary_json"], {})} for row in rows]

    def _settled_rows(self) -> list[SettledSignal]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.signal_id, COALESCE(m.league, '') AS league, COALESCE(m.market, '') AS market,
                       COALESCE(m.provider, r.provider, '') AS provider, COALESCE(m.created_at, r.created_at) AS created_at,
                       r.settled_at, COALESCE(m.confidence, 0) AS confidence, COALESCE(m.odd, 0) AS odd,
                       COALESCE(r.simulated_profit_loss, 0) AS profit_loss, COALESCE(m.apex_score, 0) AS apex_score,
                       COALESCE(m.decision, 'NO_DATA') AS decision
                FROM apex_signal_results r
                LEFT JOIN apex_signal_memory m ON m.signal_id = r.signal_id
                WHERE COALESCE(r.result_status, '') NOT IN ('', 'open', 'pending')
                ORDER BY COALESCE(r.settled_at, r.created_at) ASC
                """
            ).fetchall()
        settled: list[SettledSignal] = []
        for row in rows:
            probability = max(0.01, min(0.99, _safe_float(row["confidence"], 0.0) / 100.0))
            settled.append(
                SettledSignal(
                    signal_id=str(row["signal_id"]),
                    league=str(row["league"] or "Sem liga"),
                    market=str(row["market"] or "Sem mercado"),
                    provider=str(row["provider"] or "scanner"),
                    created_at=str(row["created_at"] or _now_iso()),
                    settled_at=str(row["settled_at"] or _now_iso()),
                    estimated_probability=probability,
                    odd=max(1.01, _safe_float(row["odd"], 1.01)),
                    profit_loss=_safe_float(row["profit_loss"], 0.0),
                    apex_score=_safe_float(row["apex_score"], 0.0),
                    decision=str(row["decision"] or "NO_DATA"),
                )
            )
        return settled

    def _group(self, rows: list[SettledSignal], attr: str) -> dict[str, list[SettledSignal]]:
        grouped: dict[str, list[SettledSignal]] = defaultdict(list)
        for row in rows:
            grouped[str(getattr(row, attr) or "Sem dado")].append(row)
        return grouped

    def _summary(self, rows: list[SettledSignal]) -> dict[str, Any]:
        volume = len(rows)
        if volume <= 0:
            return {
                "roi": 0.0,
                "hit_rate": 0.0,
                "drawdown": 0.0,
                "brier_score": 0.0,
                "log_loss": 0.0,
                "clv": 0.0,
                "yield": 0.0,
                "volume": 0,
                "stability": 0.0,
            }
        wins = sum(1 for row in rows if row.profit_loss > 0)
        total_profit = sum(row.profit_loss for row in rows)
        total_stake = max(1.0, float(volume))
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        brier = 0.0
        log_loss = 0.0
        monthly: dict[str, float] = defaultdict(float)
        for row in rows:
            equity += row.profit_loss
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
            outcome = 1.0 if row.profit_loss > 0 else 0.0
            brier += (row.estimated_probability - outcome) ** 2
            log_loss += -(
                outcome * __import__("math").log(max(row.estimated_probability, 1e-6))
                + (1 - outcome) * __import__("math").log(max(1 - row.estimated_probability, 1e-6))
            )
            monthly[row.settled_at[:7]] += row.profit_loss
        positive_months = sum(1 for profit in monthly.values() if profit >= 0)
        stability = round((positive_months / max(1, len(monthly))) * 100.0, 2)
        roi = round((total_profit / total_stake) * 100.0, 2)
        return {
            "roi": roi,
            "hit_rate": round((wins / volume) * 100.0, 2),
            "drawdown": round(abs(max_drawdown), 2),
            "brier_score": round(brier / volume, 4),
            "log_loss": round(log_loss / volume, 4),
            "clv": round(sum(max(0.0, row.apex_score - 50.0) for row in rows) / volume, 2),
            "yield": round(total_profit / total_stake, 4),
            "volume": volume,
            "stability": stability,
        }

    def _upsert_scope(self, conn: sqlite3.Connection, scope_type: str, scope_key: str, summary: dict[str, Any], created_at: str) -> None:
        score = max(0.0, min(100.0, 50.0 + summary["roi"] + (summary["stability"] * 0.2) + min(15.0, summary["volume"])))
        blocked = 1 if summary["roi"] < -8 and summary["volume"] >= 10 else 0
        if scope_type == "market":
            classification = "forte" if score >= 72 and summary["volume"] >= 8 else "ruim" if blocked else "neutro"
            conn.execute(
                """
                INSERT OR REPLACE INTO apex_market_performance (
                    market_key, market, volume, roi, drawdown, data_quality, odds_availability,
                    stability, classification, score, blocked, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_key.lower(),
                    scope_key,
                    int(summary["volume"]),
                    summary["roi"],
                    summary["drawdown"],
                    80.0,
                    100.0,
                    summary["stability"],
                    classification,
                    round(score, 1),
                    blocked,
                    created_at,
                ),
            )
        else:
            classification = "boa para operar" if score >= 72 and summary["volume"] >= 8 else "evitar" if blocked else "observacao"
            conn.execute(
                """
                INSERT OR REPLACE INTO apex_league_performance (
                    league_key, league, volume, roi, drawdown, data_quality, odds_availability,
                    stability, classification, score, blocked, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_key.lower(),
                    scope_key,
                    int(summary["volume"]),
                    summary["roi"],
                    summary["drawdown"],
                    80.0,
                    100.0,
                    summary["stability"],
                    classification,
                    round(score, 1),
                    blocked,
                    created_at,
                ),
            )


_SERVICES: dict[str, AutoBacktestService] = {}


def get_auto_backtest_service(db_file: str | Path) -> AutoBacktestService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = AutoBacktestService(key)
        _SERVICES[key] = service
    return service
