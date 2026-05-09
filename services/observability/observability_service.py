from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        data = json.loads(value)
    except Exception:
        return default
    return data if data is not None else default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ObservabilityService:
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

                CREATE TABLE IF NOT EXISTS provider_calls (
                    call_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    latency_ms REAL,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    rate_limited INTEGER NOT NULL DEFAULT 0,
                    freshness_seconds REAL,
                    args_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_calls_provider_idx ON provider_calls(provider, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_traces (
                    trace_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    findings_json TEXT NOT NULL,
                    blockers_json TEXT NOT NULL,
                    recommendation TEXT,
                    latency_ms REAL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_traces_signal_idx ON agent_traces(signal_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS decision_traces (
                    decision_trace_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    provider TEXT,
                    decision TEXT NOT NULL,
                    apex_score REAL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS decision_traces_decision_idx ON decision_traces(decision, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_events (
                    memory_event_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    event_type TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_events_signal_idx ON memory_events(signal_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS normalization_failures (
                    failure_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    market TEXT,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_signal_dispatches (
                    dispatch_id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    dispatch_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    apex_score REAL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telegram_signal_dispatches_key_idx ON telegram_signal_dispatches(dispatch_key, created_at DESC);
                CREATE INDEX IF NOT EXISTS telegram_signal_dispatches_created_idx ON telegram_signal_dispatches(created_at DESC);
                """
            )

    def log_provider_call(
        self,
        provider: str,
        operation: str,
        *,
        status: str = "ok",
        http_status: int | None = None,
        latency_ms: float | None = None,
        cache_hit: bool = False,
        retry_count: int = 0,
        rate_limited: bool = False,
        freshness_seconds: float | None = None,
        args: Any = None,
        error: str | None = None,
    ) -> str:
        call_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_calls (
                    call_id, provider, operation, status, http_status, latency_ms,
                    cache_hit, retry_count, rate_limited, freshness_seconds,
                    args_json, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    str(provider),
                    str(operation),
                    str(status),
                    http_status,
                    latency_ms,
                    1 if cache_hit else 0,
                    max(0, int(retry_count)),
                    1 if rate_limited else 0,
                    freshness_seconds,
                    _dump(args or {}),
                    str(error or ""),
                    _now_iso(),
                ),
            )
        return call_id

    def log_agent_trace(
        self,
        signal_id: str | None,
        agent_name: str,
        *,
        status: str,
        score: float,
        findings: Any = None,
        blockers: Any = None,
        recommendation: str | None = None,
        latency_ms: float | None = None,
    ) -> str:
        trace_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_traces (
                    trace_id, signal_id, agent_name, status, score, findings_json,
                    blockers_json, recommendation, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    str(signal_id or ""),
                    str(agent_name),
                    str(status),
                    float(score),
                    _dump(findings or []),
                    _dump(blockers or []),
                    str(recommendation or ""),
                    latency_ms,
                    _now_iso(),
                ),
            )
        return trace_id

    def log_decision(self, signal: dict[str, Any], *, stage: str = "prepared") -> str:
        trace_id = uuid4().hex
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        payload = {
            "stage": stage,
            "match_id": game.get("game_id") or signal.get("match_id"),
            "market": signal.get("entry_market") or signal.get("market"),
            "recommendation": signal.get("recommendation"),
            "entry_allowed": bool(signal.get("entry_allowed")),
            "apex_grade": signal.get("apex_grade"),
            "apex_reasons": signal.get("apex_reasons") or [],
            "apex_blockers": signal.get("apex_blockers") or [],
            "supervisor_decision": signal.get("supervisor_decision"),
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO decision_traces (
                    decision_trace_id, signal_id, provider, decision, apex_score, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    str(signal.get("signal_id") or signal.get("analysis_id") or ""),
                    str(signal.get("provider") or signal.get("source") or game.get("source") or ""),
                    str(signal.get("supervisor_decision") or signal.get("apex_decision") or signal.get("recommendation") or "NO_DATA"),
                    _safe_float(signal.get("apex_score"), 0.0),
                    _dump(payload),
                    _now_iso(),
                ),
            )
        return trace_id

    def log_memory_event(
        self,
        signal_id: str | None,
        *,
        event_type: str,
        operation: str,
        status: str,
        detail: str = "",
        payload: Any = None,
    ) -> str:
        event_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_events (
                    memory_event_id, signal_id, event_type, operation, status, detail, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(signal_id or ""),
                    str(event_type),
                    str(operation),
                    str(status),
                    str(detail or ""),
                    _dump(payload or {}),
                    _now_iso(),
                ),
            )
        return event_id

    def log_normalization_failure(self, provider: str, market: str | None, reason: str, *, payload: Any = None) -> str:
        failure_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO normalization_failures (
                    failure_id, provider, market, reason, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (failure_id, str(provider), str(market or ""), str(reason), _dump(payload or {}), _now_iso()),
            )
        return failure_id

    def log_telegram_dispatch(
        self,
        signal_id: str,
        chat_id: str | int,
        dispatch_key: str,
        *,
        status: str,
        decision: str,
        apex_score: float | None = None,
        payload: Any = None,
    ) -> str:
        dispatch_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_signal_dispatches (
                    dispatch_id, signal_id, chat_id, dispatch_key, status, decision, apex_score, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch_id,
                    str(signal_id),
                    str(chat_id),
                    str(dispatch_key),
                    str(status),
                    str(decision),
                    apex_score,
                    _dump(payload or {}),
                    _now_iso(),
                ),
            )
        return dispatch_id

    def has_recent_dispatch(self, dispatch_key: str, *, within_minutes: int = 5) -> bool:
        since = (_now() - timedelta(minutes=max(1, int(within_minutes)))).isoformat(timespec="seconds")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM telegram_signal_dispatches
                WHERE dispatch_key = ? AND created_at >= ?
                LIMIT 1
                """,
                (str(dispatch_key), since),
            ).fetchone()
        return bool(row)

    def dispatch_counts(self) -> dict[str, int]:
        now = _now()
        hour_since = (now - timedelta(hours=1)).isoformat(timespec="seconds")
        day_since = (now - timedelta(days=1)).isoformat(timespec="seconds")
        with self.connect() as conn:
            hour = int(conn.execute("SELECT COUNT(*) FROM telegram_signal_dispatches WHERE created_at >= ?", (hour_since,)).fetchone()[0])
            day = int(conn.execute("SELECT COUNT(*) FROM telegram_signal_dispatches WHERE created_at >= ?", (day_since,)).fetchone()[0])
        return {"last_hour": hour, "last_day": day}

    def snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            providers = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT provider,
                           COUNT(*) AS calls,
                           SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS errors,
                           AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms,
                           AVG(COALESCE(cache_hit, 0)) AS cache_hit_ratio,
                           SUM(CASE WHEN rate_limited = 1 THEN 1 ELSE 0 END) AS rate_limited,
                           MAX(created_at) AS last_seen
                    FROM provider_calls
                    GROUP BY provider
                    ORDER BY calls DESC, provider ASC
                    """
                ).fetchall()
            ]
            agents = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT agent_name,
                           COUNT(*) AS calls,
                           AVG(score) AS avg_score,
                           MAX(created_at) AS last_seen
                    FROM agent_traces
                    GROUP BY agent_name
                    ORDER BY calls DESC, agent_name ASC
                    """
                ).fetchall()
            ]
            decision_rows = conn.execute(
                """
                SELECT decision, COUNT(*) AS total
                FROM decision_traces
                GROUP BY decision
                ORDER BY total DESC, decision ASC
                """
            ).fetchall()
            telegram = self.dispatch_counts()
            latest_dispatch = conn.execute("SELECT MAX(created_at) FROM telegram_signal_dispatches").fetchone()[0]
            errors = [
                {"component": "provider", **dict(row)}
                for row in conn.execute(
                    """
                    SELECT provider AS name, error AS message, created_at
                    FROM provider_calls
                    WHERE status != 'ok' OR rate_limited = 1 OR COALESCE(error, '') != ''
                    ORDER BY created_at DESC
                    LIMIT 10
                    """
                ).fetchall()
            ]
            errors.extend(
                {
                    "component": "normalizer",
                    "name": row["provider"],
                    "message": row["reason"],
                    "created_at": row["created_at"],
                }
                for row in conn.execute(
                    """
                    SELECT provider, reason, created_at
                    FROM normalization_failures
                    ORDER BY created_at DESC
                    LIMIT 10
                    """
                ).fetchall()
            )
        return {
            "providers": providers,
            "agents": agents,
            "decisions_by_status": [dict(row) for row in decision_rows],
            "telegram": {
                **telegram,
                "last_dispatch_at": latest_dispatch,
            },
            "errors": errors[:12],
        }


_SERVICES: dict[str, ObservabilityService] = {}


def get_observability_service(db_file: str | Path) -> ObservabilityService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = ObservabilityService(key)
        _SERVICES[key] = service
    return service
