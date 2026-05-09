from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


class OperationalMemoryService:
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
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS apex_signal_memory (
                    memory_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    match_id TEXT,
                    league TEXT,
                    provider TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    odds_state TEXT NOT NULL,
                    minute INTEGER,
                    score_home INTEGER,
                    score_away INTEGER,
                    market TEXT,
                    line TEXT,
                    odd REAL,
                    ev REAL,
                    confidence REAL,
                    apex_score REAL,
                    apex_grade TEXT,
                    reason TEXT,
                    blockers_json TEXT NOT NULL,
                    data_sources_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_status TEXT,
                    simulated_profit_loss REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS apex_signal_memory_signal_idx ON apex_signal_memory(signal_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS apex_signal_memory_match_idx ON apex_signal_memory(match_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS apex_signal_memory_decision_idx ON apex_signal_memory(decision, created_at DESC);

                CREATE TABLE IF NOT EXISTS apex_signal_results (
                    result_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    match_id TEXT,
                    provider TEXT NOT NULL,
                    market TEXT,
                    result_status TEXT NOT NULL,
                    final_scoreline TEXT,
                    settled_at TEXT NOT NULL,
                    simulated_profit_loss REAL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS apex_signal_results_signal_idx ON apex_signal_results(signal_id, settled_at DESC);
                CREATE INDEX IF NOT EXISTS apex_signal_results_market_idx ON apex_signal_results(market, settled_at DESC);

                CREATE TABLE IF NOT EXISTS apex_decision_contexts (
                    context_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    context_type TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS apex_decision_contexts_signal_idx ON apex_decision_contexts(signal_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS apex_learning_events (
                    learning_event_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS apex_learning_events_type_idx ON apex_learning_events(event_type, created_at DESC);
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(apex_signal_memory)").fetchall()}
            if "league" not in columns:
                conn.execute("ALTER TABLE apex_signal_memory ADD COLUMN league TEXT")

    def record_signal_cycle(self, signal: dict[str, Any]) -> str:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        created_at = str(signal.get("created_at") or _now_iso())
        updated_at = str(signal.get("updated_at") or signal.get("last_update_at") or created_at)
        signal_id = str(signal.get("signal_id") or signal.get("analysis_id") or uuid4().hex)
        market = str(signal.get("entry_market") or signal.get("market_category") or signal.get("market") or "")
        decision = str(
            signal.get("supervisor_decision")
            or signal.get("apex_decision")
            or signal.get("recommendation")
            or signal.get("action")
            or "NO_DATA"
        )
        odd = _safe_float(signal.get("entry_odds") or signal.get("target_odds") or signal.get("odds"))
        odds_state = "confirmed" if odd and odd > 1 else "missing"
        reason = str(signal.get("why_decision") or signal.get("ai_explanation") or signal.get("reason") or "")
        blockers = (
            signal.get("apex_blockers")
            or signal.get("supervisor_blockers")
            or signal.get("decision_reasons")
            or []
        )
        data_sources = {
            "provider": signal.get("provider") or signal.get("source") or game.get("source") or "scanner",
            "historical": signal.get("historical_context") or {},
            "markets": signal.get("market_recommendations") or [],
        }
        row_id = f"{signal_id}:{created_at[:19]}"
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO apex_signal_memory (
                    memory_id, signal_id, match_id, league, provider, decision, odds_state,
                    minute, score_home, score_away, market, line, odd, ev,
                    confidence, apex_score, apex_grade, reason, blockers_json,
                    data_sources_json, payload_json, result_status, simulated_profit_loss,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    signal_id,
                    str(game.get("game_id") or signal.get("match_id") or ""),
                    str(game.get("league") or game.get("division") or signal.get("league_name") or ""),
                    str(data_sources["provider"]),
                    decision,
                    odds_state,
                    _safe_int(game.get("minute") or signal.get("minute")),
                    _safe_int(game.get("home_goals") or signal.get("home_goals")),
                    _safe_int(game.get("away_goals") or signal.get("away_goals")),
                    market,
                    str(signal.get("entry_line") or signal.get("line") or ""),
                    odd,
                    _safe_float(signal.get("expected_value")),
                    _safe_float(signal.get("confidence_score")),
                    _safe_float(signal.get("apex_score")),
                    str(signal.get("apex_grade") or ""),
                    reason,
                    _json_dump(blockers),
                    _json_dump(data_sources),
                    _json_dump(signal),
                    str(signal.get("result_status") or signal.get("outcome") or "open"),
                    _safe_float(signal.get("simulated_profit_loss") or signal.get("profit_loss")),
                    created_at,
                    updated_at,
                ),
            )
        return row_id

    def record_result(self, signal: dict[str, Any], *, result_status: str, simulated_profit_loss: float | None = None) -> str:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        signal_id = str(signal.get("signal_id") or signal.get("analysis_id") or uuid4().hex)
        result_id = f"{signal_id}:{uuid4().hex[:8]}"
        settled_at = str(signal.get("settled_at") or signal.get("updated_at") or _now_iso())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO apex_signal_results (
                    result_id, signal_id, match_id, provider, market, result_status,
                    final_scoreline, settled_at, simulated_profit_loss, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    signal_id,
                    str(game.get("game_id") or signal.get("match_id") or ""),
                    str(signal.get("provider") or signal.get("source") or game.get("source") or "scanner"),
                    str(signal.get("entry_market") or signal.get("market_category") or signal.get("market") or ""),
                    str(result_status or "unknown"),
                    f"{_safe_int(game.get('home_goals') or signal.get('home_goals'))}x{_safe_int(game.get('away_goals') or signal.get('away_goals'))}",
                    settled_at,
                    _safe_float(simulated_profit_loss if simulated_profit_loss is not None else signal.get("simulated_profit_loss")),
                    _json_dump(signal),
                    _now_iso(),
                ),
            )
        return result_id

    def record_decision_context(
        self,
        signal_id: str,
        *,
        provider: str,
        context_type: str,
        context: Any,
    ) -> str:
        context_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO apex_decision_contexts (
                    context_id, signal_id, provider, context_type, context_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context_id,
                    str(signal_id),
                    str(provider or "unknown"),
                    str(context_type or "generic"),
                    _json_dump(context),
                    _now_iso(),
                ),
            )
        return context_id

    def record_learning_event(
        self,
        *,
        signal_id: str | None = None,
        event_type: str,
        status: str,
        message: str,
        payload: Any = None,
    ) -> str:
        event_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO apex_learning_events (
                    learning_event_id, signal_id, event_type, status, message, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(signal_id or ""),
                    str(event_type or "generic"),
                    str(status or "pending"),
                    str(message or ""),
                    _json_dump(payload or {}),
                    _now_iso(),
                ),
            )
        return event_id

    def recent_signals(self, *, limit: int = 20, decision: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = """
            SELECT memory_id, signal_id, match_id, provider, decision, odds_state,
                   minute, market, odd, ev, confidence, apex_score, apex_grade,
                   reason, blockers_json, result_status, simulated_profit_loss,
                   created_at, updated_at
            FROM apex_signal_memory
        """
        if decision:
            sql += " WHERE decision = ?"
            params.append(str(decision))
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        payload: list[dict[str, Any]] = []
        for row in rows:
            payload.append({**dict(row), "blockers": json.loads(row["blockers_json"] or "[]")})
        return payload

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "apex_signal_memory": int(conn.execute("SELECT COUNT(*) FROM apex_signal_memory").fetchone()[0]),
                "apex_signal_results": int(conn.execute("SELECT COUNT(*) FROM apex_signal_results").fetchone()[0]),
                "apex_decision_contexts": int(conn.execute("SELECT COUNT(*) FROM apex_decision_contexts").fetchone()[0]),
                "apex_learning_events": int(conn.execute("SELECT COUNT(*) FROM apex_learning_events").fetchone()[0]),
            }


_SERVICES: dict[str, OperationalMemoryService] = {}


def get_operational_memory_service(db_file: str | Path) -> OperationalMemoryService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = OperationalMemoryService(key)
        _SERVICES[key] = service
    return service
