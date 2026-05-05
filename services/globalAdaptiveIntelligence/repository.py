from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class GlobalAdaptiveRepository:
    def __init__(self, db_file: str):
        self.path = Path(db_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path.as_posix(), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS data_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    provider_type TEXT NOT NULL,
                    base_url TEXT,
                    api_key_env_name TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 100,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS raw_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    source_name TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    external_ref TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS normalized_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    normalized_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS normalized_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    external_event_id TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    league TEXT,
                    season TEXT,
                    event_date TEXT NOT NULL,
                    home_label TEXT,
                    away_label TEXT,
                    status TEXT,
                    normalized_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS normalized_odds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_id TEXT,
                    sport_or_market TEXT NOT NULL,
                    market TEXT NOT NULL,
                    line TEXT,
                    source_name TEXT NOT NULL,
                    odds_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS normalized_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_id TEXT,
                    sport_or_market TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    stats_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ensemble_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    context_type TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    market TEXT NOT NULL,
                    weights_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_id TEXT,
                    market TEXT,
                    agent_name TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    trust_score REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_trust_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    agent_name TEXT NOT NULL,
                    context_type TEXT NOT NULL,
                    trust_score REAL NOT NULL,
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agent_name, context_type)
                );
                CREATE TABLE IF NOT EXISTS consensus_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_id TEXT,
                    market TEXT,
                    selection TEXT,
                    final_decision TEXT NOT NULL,
                    trust_score REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta_model_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_id TEXT,
                    market TEXT,
                    selected_model TEXT NOT NULL,
                    trust_score REAL NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monte_carlo_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    label TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    market TEXT NOT NULL,
                    paths INTEGER NOT NULL,
                    steps INTEGER NOT NULL,
                    initial_bankroll REAL NOT NULL,
                    ruin_risk REAL NOT NULL,
                    median_final_bankroll REAL NOT NULL,
                    p10_final_bankroll REAL NOT NULL,
                    p90_final_bankroll REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monte_carlo_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    run_id INTEGER NOT NULL,
                    path_index INTEGER NOT NULL,
                    final_bankroll REAL NOT NULL,
                    max_drawdown REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtest_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    label TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    sport_or_market TEXT NOT NULL,
                    market TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    strategy_version_id INTEGER,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fitness_score REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_population (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    generation INTEGER NOT NULL,
                    strategy_version_id INTEGER,
                    genome_json TEXT NOT NULL,
                    fitness_score REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS generated_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    sport_or_market TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feature_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    feature_name TEXT NOT NULL,
                    impact_score REAL NOT NULL,
                    stability_score REAL NOT NULL,
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drift_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    drift_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pattern_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    insight_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS long_term_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    risk_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exposure_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    sport_or_market TEXT NOT NULL,
                    total_exposure REAL NOT NULL,
                    risk_of_ruin REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    change_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE TABLE IF NOT EXISTS change_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    change_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rollback_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    label TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    component TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_norm_events_sport_date ON normalized_events(sport_or_market, event_date);
                CREATE INDEX IF NOT EXISTS idx_norm_events_league_season ON normalized_events(league, season);
                CREATE INDEX IF NOT EXISTS idx_norm_odds_market_created ON normalized_odds(market, created_at);
                CREATE INDEX IF NOT EXISTS idx_agent_outputs_event_market ON agent_outputs(event_id, market, created_at);
                CREATE INDEX IF NOT EXISTS idx_consensus_event_market ON consensus_decisions(event_id, market, created_at);
                CREATE INDEX IF NOT EXISTS idx_meta_model_event_market ON meta_model_decisions(event_id, market, created_at);
                CREATE INDEX IF NOT EXISTS idx_mc_results_run ON monte_carlo_results(run_id);
                CREATE INDEX IF NOT EXISTS idx_strategy_population_generation ON strategy_population(generation, created_at);
                CREATE INDEX IF NOT EXISTS idx_generated_features_name ON generated_features(feature_name, created_at);
                CREATE INDEX IF NOT EXISTS idx_drift_events_type_created ON drift_events(drift_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_search_created ON long_term_memory(memory_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_approval_status_created ON approval_requests(status, created_at);
                """
            )
            self._ensure_data_source_columns(conn)

    def _ensure_data_source_columns(self, conn: sqlite3.Connection) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(data_sources)").fetchall()}
        wanted = {
            "domain": "TEXT",
            "sport_or_market": "TEXT",
            "rate_limit_per_minute": "INTEGER NOT NULL DEFAULT 0",
            "requires_api_key": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'ready'",
            "notes": "TEXT",
        }
        for column, ddl in wanted.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE data_sources ADD COLUMN {column} {ddl}")

    def log(self, component: str, message: str, *, payload: dict[str, Any] | None = None, level: str = "info", user_id: int | None = None) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_logs (user_id, component, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, component[:120], message[:500], _json(payload or {}), now),
            )
            conn.execute(
                """
                INSERT INTO system_logs (user_id, component, level, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, component[:120], level[:20], message[:500], _json(payload or {}), now),
            )

    def seed_data_sources(self, rows: list[dict[str, Any]]) -> None:
        now = _now_iso()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO data_sources (
                        name, provider_type, base_url, api_key_env_name, is_active, priority, domain,
                        sport_or_market, rate_limit_per_minute, requires_api_key, status, notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        provider_type = excluded.provider_type,
                        base_url = excluded.base_url,
                        api_key_env_name = excluded.api_key_env_name,
                        is_active = excluded.is_active,
                        priority = excluded.priority,
                        domain = excluded.domain,
                        sport_or_market = excluded.sport_or_market,
                        rate_limit_per_minute = excluded.rate_limit_per_minute,
                        requires_api_key = excluded.requires_api_key,
                        status = excluded.status,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row["name"],
                        row["provider_type"],
                        row.get("base_url"),
                        row.get("api_key_env_name"),
                        1 if row.get("is_active", True) else 0,
                        int(row.get("priority", 100)),
                        row.get("domain"),
                        row.get("sport_or_market") or "football",
                        int(row.get("rate_limit_per_minute", 0) or 0),
                        1 if row.get("requires_api_key") else 0,
                        row.get("status") or "ready",
                        row.get("notes"),
                        now,
                        now,
                    ),
                )

    def list_data_sources(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM data_sources ORDER BY priority ASC, name ASC").fetchall()
        return [dict(row) for row in rows]

    def save_generated_features(self, rows: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        now = _now_iso()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO generated_features (user_id, sport_or_market, feature_name, scope, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, row.get("sport_or_market", "football"), row["feature_name"], row.get("scope", "global"), _json(row), now),
                )

    def list_generated_features(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM generated_features ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_ensemble_config(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO ensemble_configs (
                    user_id, name, version, context_type, sport_or_market, market, weights_json, is_active, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["name"][:120],
                    payload.get("version", "v1")[:40],
                    payload.get("context_type", "default")[:80],
                    payload.get("sport_or_market", "football")[:80],
                    payload.get("market", "match_winner_home")[:120],
                    _json(payload.get("weights", {})),
                    1 if payload.get("is_active") else 0,
                    payload.get("status", "draft")[:20],
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def list_ensemble_configs(self, *, sport_or_market: str | None = None, market: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ensemble_configs WHERE 1=1"
        params: list[Any] = []
        if sport_or_market:
            query += " AND sport_or_market = ?"
            params.append(sport_or_market)
        if market:
            query += " AND market = ?"
            params.append(market)
        query += " ORDER BY is_active DESC, created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["weights"] = _loads(item.pop("weights_json", None), {})
        return items

    def save_agent_output(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_outputs (user_id, event_id, market, agent_name, decision, trust_score, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload.get("event_id"),
                    payload.get("market"),
                    payload["agent_name"][:80],
                    payload.get("decision", "HOLD")[:40],
                    float(payload.get("trust_score", 0.5) or 0.5),
                    _json(payload),
                    _now_iso(),
                ),
            )

    def upsert_agent_trust(self, agent_name: str, context_type: str, trust_score: float, *, sample_size: int = 0, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_trust_scores (user_id, agent_name, context_type, trust_score, sample_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_name, context_type) DO UPDATE SET
                    trust_score = excluded.trust_score,
                    sample_size = excluded.sample_size,
                    updated_at = excluded.updated_at
                """,
                (user_id, agent_name[:80], context_type[:80], float(trust_score), int(sample_size), _now_iso()),
            )

    def list_agent_trust_scores(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM agent_trust_scores ORDER BY trust_score DESC, updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def save_consensus_decision(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO consensus_decisions (user_id, event_id, market, selection, final_decision, trust_score, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload.get("event_id"),
                    payload.get("market"),
                    payload.get("selection"),
                    payload.get("final_decision", "NO_BET")[:40],
                    float(payload.get("trust_score", 0.0) or 0.0),
                    _json(payload.get("reasons", [])),
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def list_consensus_decisions(self, *, limit: int = 40) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM consensus_decisions ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["reasons"] = _loads(item.pop("reasons_json", None), [])
        return items

    def save_meta_model_decision(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO meta_model_decisions (user_id, event_id, market, selected_model, trust_score, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload.get("event_id"),
                    payload.get("market"),
                    payload["selected_model"][:120],
                    float(payload.get("trust_score", 0.0) or 0.0),
                    payload.get("reason", "")[:500],
                    _json(payload),
                    _now_iso(),
                ),
            )

    def save_monte_carlo_run(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO monte_carlo_runs (
                    user_id, label, sport_or_market, market, paths, steps, initial_bankroll, ruin_risk,
                    median_final_bankroll, p10_final_bankroll, p90_final_bankroll, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["label"][:160],
                    payload.get("sport_or_market", "football")[:80],
                    payload.get("market", "match_winner_home")[:120],
                    int(payload.get("paths", 0)),
                    int(payload.get("steps", 0)),
                    float(payload.get("initial_bankroll", 0.0)),
                    float(payload.get("ruin_risk", 0.0)),
                    float(payload.get("median_final_bankroll", 0.0)),
                    float(payload.get("p10_final_bankroll", 0.0)),
                    float(payload.get("p90_final_bankroll", 0.0)),
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def save_monte_carlo_results(self, run_id: int, rows: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO monte_carlo_results (user_id, run_id, path_index, final_bankroll, max_drawdown, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        int(run_id),
                        int(row.get("path_index", 0)),
                        float(row.get("final_bankroll", 0.0)),
                        float(row.get("max_drawdown", 0.0)),
                        _json(row),
                        _now_iso(),
                    ),
                )

    def list_monte_carlo_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM monte_carlo_runs ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def save_strategy_version(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO strategy_versions (user_id, name, version, sport_or_market, market, rules_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["name"][:120],
                    payload.get("version", "v1")[:40],
                    payload.get("sport_or_market", "football")[:80],
                    payload.get("market", "match_winner_home")[:120],
                    _json(payload.get("rules", {})),
                    payload.get("status", "draft")[:20],
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def save_strategy_population(self, rows: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO strategy_population (user_id, generation, strategy_version_id, genome_json, fitness_score, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        int(row.get("generation", 0)),
                        row.get("strategy_version_id"),
                        _json(row.get("genome", {})),
                        float(row.get("fitness_score", 0.0)),
                        _now_iso(),
                    ),
                )

    def list_strategy_population(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_population ORDER BY fitness_score DESC, created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["genome"] = _loads(item.pop("genome_json", None), {})
        return items

    def save_drift_event(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO drift_events (user_id, drift_type, scope, severity, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["drift_type"][:80],
                    payload.get("scope", "global")[:120],
                    payload.get("severity", "low")[:20],
                    _json(payload),
                    _now_iso(),
                ),
            )

    def list_drift_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM drift_events ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def save_pattern_insight(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pattern_insights (user_id, insight_type, label, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, payload["insight_type"][:80], payload["label"][:200], _json(payload), _now_iso()),
            )

    def list_pattern_insights(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM pattern_insights ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def save_long_term_memory(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO long_term_memory (user_id, memory_type, title, body, search_text, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["memory_type"][:80],
                    payload["title"][:220],
                    payload["body"],
                    str(payload.get("search_text") or payload["body"]).lower(),
                    _json(payload),
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def search_long_term_memory(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        term = f"%{str(query or '').strip().lower()}%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM long_term_memory
                WHERE search_text LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (term, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_risk_event(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO risk_events (user_id, risk_type, severity, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, payload["risk_type"][:80], payload.get("severity", "low")[:20], _json(payload), _now_iso()),
            )

    def list_risk_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM risk_events ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def save_exposure_snapshot(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO exposure_snapshots (user_id, sport_or_market, total_exposure, risk_of_ruin, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload.get("sport_or_market", "football")[:80],
                    float(payload.get("total_exposure", 0.0)),
                    float(payload.get("risk_of_ruin", 0.0)),
                    _json(payload),
                    _now_iso(),
                ),
            )

    def list_exposure_snapshots(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM exposure_snapshots ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(row) for row in rows]

    def create_approval_request(self, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO approval_requests (user_id, change_type, target_ref, status, payload_json, created_at, decided_at)
                VALUES (?, ?, ?, 'pending', ?, ?, NULL)
                """,
                (user_id, payload["change_type"][:80], payload["target_ref"][:160], _json(payload), _now_iso()),
            )
            return int(cur.lastrowid)

    def decide_approval_request(self, request_id: int, decision: str, *, user_id: int | None = None) -> dict[str, Any] | None:
        clean = (decision or "").strip().lower()
        if clean not in {"approved", "rejected", "applied", "rolled_back"}:
            clean = "rejected"
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE approval_requests SET status = ?, decided_at = ? WHERE id = ?",
                (clean, now, int(request_id)),
            )
            row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (int(request_id),)).fetchone()
        if row:
            self.save_change_history(
                {
                    "change_type": row["change_type"],
                    "target_ref": row["target_ref"],
                    "action": clean,
                    "payload": _loads(row["payload_json"], {}),
                },
                user_id=user_id,
            )
        return dict(row) if row else None

    def list_approval_requests(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM approval_requests"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["payload"] = _loads(item.pop("payload_json", None), {})
        return items

    def save_change_history(self, payload: dict[str, Any], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO change_history (user_id, change_type, target_ref, action, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload["change_type"][:80],
                    payload["target_ref"][:160],
                    payload["action"][:80],
                    _json(payload.get("payload", {})),
                    _now_iso(),
                ),
            )

    def create_rollback_point(self, label: str, payload: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO rollback_points (user_id, label, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, label[:180], _json(payload), _now_iso()),
            )
            return int(cur.lastrowid)

    def list_rollback_points(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM rollback_points ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["payload"] = _loads(item.pop("payload_json", None), {})
        return items

    def snapshot(self) -> dict[str, Any]:
        tables = [
            "data_sources",
            "normalized_events",
            "normalized_odds",
            "normalized_stats",
            "ensemble_configs",
            "agent_outputs",
            "consensus_decisions",
            "monte_carlo_runs",
            "strategy_versions",
            "strategy_population",
            "generated_features",
            "drift_events",
            "pattern_insights",
            "long_term_memory",
            "risk_events",
            "approval_requests",
        ]
        counts: dict[str, int] = {}
        with self.connect() as conn:
            for table in tables:
                row = conn.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
                counts[table] = int(row["total"] if row else 0)
        return {"db_file": self.path.as_posix(), "counts": counts}

