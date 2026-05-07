from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .schemas import MatchPrediction, NormalizedMatch, SourceRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class FootballResearchRepository:
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
                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    external_id TEXT,
                    name TEXT NOT NULL,
                    country TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(external_id, source)
                );
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    external_id TEXT NOT NULL,
                    league TEXT NOT NULL,
                    country TEXT,
                    season INTEGER,
                    match_date TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    status TEXT NOT NULL,
                    minute INTEGER,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(external_id, source)
                );
                CREATE TABLE IF NOT EXISTS match_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id INTEGER NOT NULL,
                    possession_home REAL,
                    possession_away REAL,
                    shots_home INTEGER,
                    shots_away INTEGER,
                    shots_on_home INTEGER,
                    shots_on_away INTEGER,
                    corners_home INTEGER,
                    corners_away INTEGER,
                    yellow_home INTEGER,
                    yellow_away INTEGER,
                    red_home INTEGER,
                    red_away INTEGER,
                    dangerous_attacks_home INTEGER,
                    dangerous_attacks_away INTEGER,
                    attacks_home INTEGER,
                    attacks_away INTEGER,
                    xg_home REAL,
                    xg_away REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(match_id)
                );
                CREATE TABLE IF NOT EXISTS odds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    market TEXT NOT NULL,
                    line TEXT,
                    home_odd REAL,
                    draw_odd REAL,
                    away_odd REAL,
                    over_odd REAL,
                    under_odd REAL,
                    bookmaker TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id INTEGER NOT NULL,
                    model_version_id INTEGER,
                    market TEXT NOT NULL,
                    estimated_probability REAL NOT NULL,
                    fair_odd REAL,
                    offered_odd REAL,
                    expected_value REAL,
                    confidence_score REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    explanation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bets_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    prediction_id INTEGER NOT NULL,
                    bankroll_profile TEXT NOT NULL,
                    suggested_stake REAL NOT NULL,
                    kelly_fraction REAL NOT NULL,
                    no_bet_reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bankroll_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    bankroll REAL NOT NULL DEFAULT 1000,
                    profile TEXT NOT NULL DEFAULT 'moderado',
                    profile_multiplier REAL NOT NULL DEFAULT 0.5,
                    max_stake_pct REAL NOT NULL DEFAULT 0.03,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id)
                );
                CREATE TABLE IF NOT EXISTS model_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    rules_json TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS model_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    model_version_id INTEGER,
                    scope TEXT NOT NULL,
                    league TEXT,
                    market TEXT,
                    total_entries INTEGER NOT NULL DEFAULT 0,
                    hit_rate REAL NOT NULL DEFAULT 0,
                    roi REAL NOT NULL DEFAULT 0,
                    profit_loss REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    external_id TEXT NOT NULL,
                    external_fixture_id TEXT,
                    source_provider TEXT,
                    league_id TEXT,
                    league TEXT NOT NULL,
                    league_name TEXT,
                    country TEXT,
                    season INTEGER,
                    match_date TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    status TEXT NOT NULL,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    normalized_payload TEXT,
                    data_quality_score INTEGER NOT NULL DEFAULT 0,
                    usable_for_training INTEGER NOT NULL DEFAULT 0,
                    duplicate_key TEXT,
                    temporal_split TEXT,
                    import_batch_id TEXT,
                    imported_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(external_id, source)
                );
                CREATE TABLE IF NOT EXISTS historical_odds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    historical_match_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    market TEXT NOT NULL,
                    line TEXT,
                    home_odd REAL,
                    draw_odd REAL,
                    away_odd REAL,
                    over_odd REAL,
                    under_odd REAL,
                    bookmaker TEXT,
                    source TEXT NOT NULL,
                    odds_phase TEXT NOT NULL DEFAULT 'pregame',
                    is_real INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    imported_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    historical_match_id INTEGER NOT NULL,
                    possession_home REAL,
                    possession_away REAL,
                    shots_home INTEGER,
                    shots_away INTEGER,
                    shots_on_home INTEGER,
                    shots_on_away INTEGER,
                    corners_home INTEGER,
                    corners_away INTEGER,
                    yellow_home INTEGER,
                    yellow_away INTEGER,
                    red_home INTEGER,
                    red_away INTEGER,
                    dangerous_attacks_home INTEGER,
                    dangerous_attacks_away INTEGER,
                    attacks_home INTEGER,
                    attacks_away INTEGER,
                    xg_home REAL,
                    xg_away REAL,
                    raw_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(historical_match_id)
                );
                CREATE TABLE IF NOT EXISTS historical_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id INTEGER NOT NULL,
                    feature_set_version TEXT NOT NULL,
                    temporal_split TEXT,
                    home_recent_form_5 REAL,
                    away_recent_form_5 REAL,
                    home_goals_avg_5 REAL,
                    away_goals_avg_5 REAL,
                    home_conceded_avg_5 REAL,
                    away_conceded_avg_5 REAL,
                    home_xg_avg_5 REAL,
                    away_xg_avg_5 REAL,
                    home_strength REAL,
                    away_strength REAL,
                    market_implied_probability REAL,
                    closing_line_value REAL,
                    data_quality_score INTEGER NOT NULL DEFAULT 0,
                    usable_for_training INTEGER NOT NULL DEFAULT 0,
                    context_match_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(match_id, feature_set_version)
                );
                CREATE TABLE IF NOT EXISTS league_reliability_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    league TEXT NOT NULL,
                    season INTEGER,
                    match_count INTEGER NOT NULL DEFAULT 0,
                    trainable_count INTEGER NOT NULL DEFAULT 0,
                    odds_count INTEGER NOT NULL DEFAULT 0,
                    stats_count INTEGER NOT NULL DEFAULT 0,
                    avg_data_quality REAL NOT NULL DEFAULT 0,
                    roi_simulated REAL NOT NULL DEFAULT 0,
                    drawdown REAL NOT NULL DEFAULT 0,
                    stability_score REAL NOT NULL DEFAULT 0,
                    league_reliability_score REAL NOT NULL DEFAULT 0,
                    classification TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    calculated_at TEXT NOT NULL,
                    UNIQUE(league, season)
                );
                CREATE TABLE IF NOT EXISTS historical_import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_key TEXT NOT NULL UNIQUE,
                    source_provider TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    imported_matches INTEGER NOT NULL DEFAULT 0,
                    duplicates_blocked INTEGER NOT NULL DEFAULT 0,
                    errors_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    model_version_id INTEGER,
                    label TEXT NOT NULL,
                    league TEXT,
                    market TEXT,
                    season INTEGER,
                    date_from TEXT,
                    date_to TEXT,
                    initial_bankroll REAL NOT NULL,
                    final_bankroll REAL NOT NULL,
                    total_games INTEGER NOT NULL,
                    total_entries INTEGER NOT NULL,
                    hit_rate REAL NOT NULL,
                    roi REAL NOT NULL,
                    drawdown REAL NOT NULL,
                    profit_loss REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS simulation_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    simulation_run_id INTEGER NOT NULL,
                    historical_match_id INTEGER NOT NULL,
                    prediction_id INTEGER,
                    market TEXT NOT NULL,
                    offered_odd REAL,
                    fair_odd REAL,
                    expected_value REAL,
                    stake REAL NOT NULL,
                    result TEXT NOT NULL,
                    profit_loss REAL NOT NULL,
                    bankroll_after REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_type TEXT NOT NULL,
                    ref_type TEXT,
                    ref_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    version_name TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS strategy_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    strategy_rule_id INTEGER,
                    suggestion_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    body TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    document_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    tokens_estimate INTEGER NOT NULL DEFAULT 0,
                    search_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS raw_football_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    source_name TEXT NOT NULL,
                    external_ref TEXT,
                    payload_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS normalized_football_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    entity_type TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    normalized_json TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS football_research_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    level TEXT NOT NULL,
                    component TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_corners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    historical_match_id INTEGER,
                    external_fixture_id TEXT,
                    source_provider TEXT NOT NULL,
                    period TEXT NOT NULL DEFAULT 'FT',
                    corners_home INTEGER,
                    corners_away INTEGER,
                    corners_total INTEGER,
                    line TEXT,
                    over_odd REAL,
                    under_odd REAL,
                    bookmaker TEXT,
                    is_real INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    imported_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    historical_match_id INTEGER,
                    external_fixture_id TEXT,
                    source_provider TEXT NOT NULL,
                    period TEXT NOT NULL DEFAULT 'FT',
                    yellow_home INTEGER,
                    yellow_away INTEGER,
                    red_home INTEGER,
                    red_away INTEGER,
                    cards_total REAL,
                    line TEXT,
                    over_odd REAL,
                    under_odd REAL,
                    bookmaker TEXT,
                    referee_name TEXT,
                    is_real INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    imported_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS historical_asian_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    historical_match_id INTEGER,
                    external_fixture_id TEXT,
                    source_provider TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    period TEXT NOT NULL DEFAULT 'FT',
                    line TEXT,
                    home_odd REAL,
                    away_odd REAL,
                    over_odd REAL,
                    under_odd REAL,
                    bookmaker TEXT,
                    is_real INTEGER NOT NULL DEFAULT 1,
                    raw_json TEXT,
                    imported_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_pressure_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id TEXT NOT NULL,
                    source_provider TEXT,
                    captured_at TEXT NOT NULL,
                    minute INTEGER,
                    pressure_home REAL,
                    pressure_away REAL,
                    momentum_score REAL,
                    territorial_dominance TEXT,
                    shots_on_home INTEGER,
                    shots_on_away INTEGER,
                    dangerous_attacks_home INTEGER,
                    dangerous_attacks_away INTEGER,
                    corners_home INTEGER,
                    corners_away INTEGER,
                    raw_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS referee_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    referee_name TEXT NOT NULL,
                    league TEXT,
                    country TEXT,
                    matches_count INTEGER NOT NULL DEFAULT 0,
                    cards_avg REAL,
                    yellow_avg REAL,
                    red_avg REAL,
                    fouls_avg REAL,
                    cards_ht_avg REAL,
                    cards_st_avg REAL,
                    aggression_index REAL,
                    source_provider TEXT,
                    raw_json TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(referee_name, league, country)
                );
                CREATE TABLE IF NOT EXISTS live_market_movements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    match_id TEXT NOT NULL,
                    source_provider TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    selection TEXT,
                    line TEXT,
                    period TEXT NOT NULL DEFAULT 'FT',
                    odd REAL,
                    previous_odd REAL,
                    movement REAL,
                    steam_detected INTEGER NOT NULL DEFAULT 0,
                    liquidity_status TEXT,
                    raw_json TEXT,
                    captured_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_matches_league_season_date ON matches(league, season, match_date);
                CREATE INDEX IF NOT EXISTS idx_odds_market_created ON odds(market, created_at);
                CREATE INDEX IF NOT EXISTS idx_predictions_created ON predictions(created_at);
                CREATE INDEX IF NOT EXISTS idx_hist_matches_league_season_date ON historical_matches(league, season, match_date);
                CREATE INDEX IF NOT EXISTS idx_hist_odds_market ON historical_odds(market, timestamp);
                CREATE INDEX IF NOT EXISTS idx_hist_features_match_version ON historical_features(match_id, feature_set_version);
                CREATE INDEX IF NOT EXISTS idx_league_reliability_score ON league_reliability_scores(league_reliability_score, classification);
                CREATE INDEX IF NOT EXISTS idx_simulation_results_run ON simulation_results(simulation_run_id);
                CREATE INDEX IF NOT EXISTS idx_model_performance_version ON model_performance(model_version_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_strategy_suggestions_status ON strategy_suggestions(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(document_id, chunk_index);
                CREATE INDEX IF NOT EXISTS idx_research_logs_component ON football_research_logs(component, created_at);
                CREATE INDEX IF NOT EXISTS idx_historical_corners_match ON historical_corners(historical_match_id, period);
                CREATE INDEX IF NOT EXISTS idx_historical_cards_match ON historical_cards(historical_match_id, period);
                CREATE INDEX IF NOT EXISTS idx_historical_asian_match ON historical_asian_lines(historical_match_id, market_type, period);
                CREATE INDEX IF NOT EXISTS idx_market_pressure_match_time ON market_pressure_snapshots(match_id, captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_referee_profiles_league ON referee_profiles(league, referee_name);
                CREATE INDEX IF NOT EXISTS idx_live_market_movements_match ON live_market_movements(match_id, market_type, captured_at DESC);
                """
            )
            self._ensure_column(conn, "historical_matches", "external_fixture_id", "TEXT")
            self._ensure_column(conn, "historical_matches", "source_provider", "TEXT")
            self._ensure_column(conn, "historical_matches", "league_id", "TEXT")
            self._ensure_column(conn, "historical_matches", "league_name", "TEXT")
            self._ensure_column(conn, "historical_matches", "normalized_payload", "TEXT")
            self._ensure_column(conn, "historical_matches", "data_quality_score", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "historical_matches", "usable_for_training", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "historical_matches", "duplicate_key", "TEXT")
            self._ensure_column(conn, "historical_matches", "temporal_split", "TEXT")
            self._ensure_column(conn, "historical_matches", "import_batch_id", "TEXT")
            self._ensure_column(conn, "historical_matches", "imported_at", "TEXT")
            self._ensure_column(conn, "historical_odds", "odds_phase", "TEXT NOT NULL DEFAULT 'pregame'")
            self._ensure_column(conn, "historical_odds", "is_real", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "historical_odds", "raw_json", "TEXT")
            self._ensure_column(conn, "historical_odds", "imported_at", "TEXT")
            self._ensure_column(conn, "historical_stats", "raw_json", "TEXT")
            conn.executescript(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_matches_source_fixture
                    ON historical_matches(source_provider, external_fixture_id)
                    WHERE source_provider IS NOT NULL AND external_fixture_id IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_hist_matches_duplicate_key
                    ON historical_matches(duplicate_key)
                    WHERE duplicate_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_hist_matches_quality
                    ON historical_matches(data_quality_score, usable_for_training);
                CREATE INDEX IF NOT EXISTS idx_hist_matches_split
                    ON historical_matches(temporal_split, match_date);
                """
            )

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def log(self, component: str, message: str, *, level: str = "info", payload: dict[str, Any] | None = None, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO football_research_logs (user_id, level, component, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, level, component, message[:600], _json(payload or {}), _now_iso()),
            )

    def seed_data_sources(self, rows: list[SourceRecord]) -> None:
        now = _now_iso()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO data_sources (name, provider_type, base_url, api_key_env_name, is_active, priority, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        provider_type = excluded.provider_type,
                        base_url = excluded.base_url,
                        api_key_env_name = excluded.api_key_env_name,
                        priority = excluded.priority,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row.name,
                        row.provider_type,
                        row.base_url,
                        row.api_key_env_name,
                        1 if row.is_active else 0,
                        row.priority,
                        now,
                        now,
                    ),
                )

    def list_data_sources(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM data_sources ORDER BY priority ASC, name ASC").fetchall()
        return [dict(row) for row in rows]

    def set_data_source_status(self, name: str, is_active: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE data_sources SET is_active = ?, updated_at = ? WHERE name = ?",
                (1 if is_active else 0, _now_iso(), name),
            )

    def ensure_bankroll_settings(self, user_id: int | None, bankroll: float, profile: str, multiplier: float) -> dict[str, Any]:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bankroll_settings (user_id, bankroll, profile, profile_multiplier, max_stake_pct, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0.03, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    bankroll = excluded.bankroll,
                    profile = excluded.profile,
                    profile_multiplier = excluded.profile_multiplier,
                    updated_at = excluded.updated_at
                """,
                (user_id, bankroll, profile, multiplier, now, now),
            )
            row = conn.execute("SELECT * FROM bankroll_settings WHERE user_id IS ?", (user_id,)).fetchone()
        return dict(row) if row else {}

    def import_normalized_matches(self, matches: list[NormalizedMatch], *, source_name: str, user_id: int | None = None) -> dict[str, int]:
        inserted = 0
        now = _now_iso()
        with self.connect() as conn:
            for item in matches:
                payload = asdict(item) if is_dataclass(item) else dict(item)
                normalized_payload = _json(payload)
                data_quality_score = _historical_data_quality(payload)
                duplicate_key = _historical_duplicate_key(
                    source=str(item.source or source_name),
                    external_id=str(item.external_id or ""),
                    league=str(item.league or ""),
                    season=item.season,
                    match_date=item.match_date.isoformat(),
                    home_team=str(item.home_team or ""),
                    away_team=str(item.away_team or ""),
                )
                conn.execute(
                    """
                    INSERT INTO raw_football_imports (user_id, source_name, external_ref, payload_json, imported_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, source_name, item.external_id, _json(payload), now),
                )
                conn.execute(
                    """
                    INSERT INTO normalized_football_data (user_id, entity_type, entity_key, normalized_json, source_name, created_at)
                    VALUES (?, 'match', ?, ?, ?, ?)
                    """,
                    (user_id, f"{source_name}:{item.external_id}", _json(payload), source_name, now),
                )
                conn.execute(
                    """
                    INSERT INTO historical_matches (
                        user_id, external_id, external_fixture_id, source_provider, league_id, league, league_name,
                        country, season, match_date, home_team, away_team, status, home_goals, away_goals,
                        source, raw_json, normalized_payload, data_quality_score, usable_for_training,
                        duplicate_key, imported_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_id, source) DO UPDATE SET
                        external_fixture_id=excluded.external_fixture_id,
                        source_provider=excluded.source_provider,
                        league_id=excluded.league_id,
                        league=excluded.league,
                        league_name=excluded.league_name,
                        country=excluded.country,
                        season=excluded.season,
                        match_date=excluded.match_date,
                        home_team=excluded.home_team,
                        away_team=excluded.away_team,
                        status=excluded.status,
                        home_goals=excluded.home_goals,
                        away_goals=excluded.away_goals,
                        raw_json=excluded.raw_json,
                        normalized_payload=excluded.normalized_payload,
                        data_quality_score=excluded.data_quality_score,
                        usable_for_training=excluded.usable_for_training,
                        duplicate_key=excluded.duplicate_key,
                        updated_at=excluded.updated_at
                    """,
                    (
                        user_id,
                        item.external_id,
                        item.external_id,
                        item.source or source_name,
                        str((item.raw_payload or {}).get("league", {}).get("id") or "") or None,
                        item.league,
                        item.league,
                        item.country,
                        item.season,
                        item.match_date.isoformat(),
                        item.home_team,
                        item.away_team,
                        item.status,
                        item.home_goals,
                        item.away_goals,
                        item.source,
                        _json(item.raw_payload or payload),
                        normalized_payload,
                        data_quality_score,
                        1 if data_quality_score >= 70 else 0,
                        duplicate_key,
                        now,
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM historical_matches WHERE external_id = ? AND source = ?",
                    (item.external_id, item.source),
                ).fetchone()
                hist_id = int(row["id"])
                if item.stats:
                    stats = item.stats
                    conn.execute(
                        """
                        INSERT INTO historical_stats (
                            user_id, historical_match_id, possession_home, possession_away, shots_home, shots_away,
                            shots_on_home, shots_on_away, corners_home, corners_away, yellow_home, yellow_away,
                            red_home, red_away, dangerous_attacks_home, dangerous_attacks_away, attacks_home, attacks_away,
                            xg_home, xg_away, raw_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(historical_match_id) DO UPDATE SET
                            possession_home=excluded.possession_home,
                            possession_away=excluded.possession_away,
                            shots_home=excluded.shots_home,
                            shots_away=excluded.shots_away,
                            shots_on_home=excluded.shots_on_home,
                            shots_on_away=excluded.shots_on_away,
                            corners_home=excluded.corners_home,
                            corners_away=excluded.corners_away,
                            yellow_home=excluded.yellow_home,
                            yellow_away=excluded.yellow_away,
                            red_home=excluded.red_home,
                            red_away=excluded.red_away,
                            dangerous_attacks_home=excluded.dangerous_attacks_home,
                            dangerous_attacks_away=excluded.dangerous_attacks_away,
                            attacks_home=excluded.attacks_home,
                            attacks_away=excluded.attacks_away,
                            xg_home=excluded.xg_home,
                            xg_away=excluded.xg_away,
                            raw_json=excluded.raw_json,
                            updated_at=excluded.updated_at
                        """,
                        (
                            user_id,
                            hist_id,
                            stats.get("possession_home"),
                            stats.get("possession_away"),
                            stats.get("shots_home"),
                            stats.get("shots_away"),
                            stats.get("shots_on_home"),
                            stats.get("shots_on_away"),
                            stats.get("corners_home"),
                            stats.get("corners_away"),
                            stats.get("yellow_home"),
                            stats.get("yellow_away"),
                            stats.get("red_home"),
                            stats.get("red_away"),
                            stats.get("dangerous_attacks_home"),
                            stats.get("dangerous_attacks_away"),
                            stats.get("attacks_home"),
                            stats.get("attacks_away"),
                            stats.get("xg_home"),
                            stats.get("xg_away"),
                            _json(stats),
                            now,
                            now,
                        ),
                    )
                if item.odds:
                    conn.execute("DELETE FROM historical_odds WHERE historical_match_id = ?", (hist_id,))
                    for odd in item.odds:
                        conn.execute(
                            """
                            INSERT INTO historical_odds (
                                user_id, historical_match_id, timestamp, market, line,
                                home_odd, draw_odd, away_odd, over_odd, under_odd, bookmaker, source, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                user_id,
                                hist_id,
                                str(odd.get("timestamp") or item.match_date.isoformat()),
                                str(odd.get("market") or "match_winner"),
                                odd.get("line"),
                                odd.get("home_odd"),
                                odd.get("draw_odd"),
                                odd.get("away_odd"),
                                odd.get("over_odd"),
                                odd.get("under_odd"),
                                odd.get("bookmaker"),
                                str(odd.get("source") or source_name),
                                now,
                            ),
                        )
                inserted += 1
        return {"imported_matches": inserted}

    def list_historical_matches(self, *, league: str | None = None, season: int | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        query = "SELECT * FROM historical_matches WHERE 1=1"
        params: list[Any] = []
        if league:
            query += " AND league = ?"
            params.append(league)
        if season is not None:
            query += " AND season = ?"
            params.append(season)
        query += " ORDER BY match_date DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_historical_match(self, historical_match_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            match = conn.execute("SELECT * FROM historical_matches WHERE id = ?", (historical_match_id,)).fetchone()
            if not match:
                return None
            stats = conn.execute("SELECT * FROM historical_stats WHERE historical_match_id = ?", (historical_match_id,)).fetchone()
            odds = conn.execute("SELECT * FROM historical_odds WHERE historical_match_id = ? ORDER BY timestamp DESC", (historical_match_id,)).fetchall()
        payload = dict(match)
        payload["stats"] = dict(stats) if stats else None
        payload["odds"] = [dict(item) for item in odds]
        payload["raw"] = _loads(payload.get("raw_json"), {})
        return payload

    def upsert_historical_features(self, features: list[dict[str, Any]], *, user_id: int | None = None) -> int:
        if not features:
            return 0
        imported = 0
        with self.connect() as conn:
            for item in features:
                match_id = int(item.get("match_id") or 0)
                feature_set_version = str(item.get("feature_set_version") or "supabase")
                if match_id <= 0 or not feature_set_version:
                    continue
                conn.execute(
                    """
                    INSERT INTO historical_features (
                        user_id, match_id, feature_set_version, temporal_split,
                        home_recent_form_5, away_recent_form_5,
                        home_goals_avg_5, away_goals_avg_5,
                        home_conceded_avg_5, away_conceded_avg_5,
                        home_xg_avg_5, away_xg_avg_5,
                        home_strength, away_strength,
                        market_implied_probability, closing_line_value,
                        data_quality_score, usable_for_training, context_match_count,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_id, feature_set_version) DO UPDATE SET
                        temporal_split = excluded.temporal_split,
                        home_recent_form_5 = excluded.home_recent_form_5,
                        away_recent_form_5 = excluded.away_recent_form_5,
                        home_goals_avg_5 = excluded.home_goals_avg_5,
                        away_goals_avg_5 = excluded.away_goals_avg_5,
                        home_conceded_avg_5 = excluded.home_conceded_avg_5,
                        away_conceded_avg_5 = excluded.away_conceded_avg_5,
                        home_xg_avg_5 = excluded.home_xg_avg_5,
                        away_xg_avg_5 = excluded.away_xg_avg_5,
                        home_strength = excluded.home_strength,
                        away_strength = excluded.away_strength,
                        market_implied_probability = excluded.market_implied_probability,
                        closing_line_value = excluded.closing_line_value,
                        data_quality_score = excluded.data_quality_score,
                        usable_for_training = excluded.usable_for_training,
                        context_match_count = excluded.context_match_count,
                        created_at = excluded.created_at
                    """,
                    (
                        user_id,
                        match_id,
                        feature_set_version,
                        item.get("temporal_split"),
                        item.get("home_recent_form_5"),
                        item.get("away_recent_form_5"),
                        item.get("home_goals_avg_5"),
                        item.get("away_goals_avg_5"),
                        item.get("home_conceded_avg_5"),
                        item.get("away_conceded_avg_5"),
                        item.get("home_xg_avg_5"),
                        item.get("away_xg_avg_5"),
                        item.get("home_strength"),
                        item.get("away_strength"),
                        item.get("market_implied_probability"),
                        item.get("closing_line_value"),
                        int(item.get("data_quality_score") or 0),
                        1 if item.get("usable_for_training") else 0,
                        int(item.get("context_match_count") or 0),
                        str(item.get("created_at") or _now_iso()),
                    ),
                )
                imported += 1
        return imported

    def upsert_league_reliability_scores(self, rows: list[dict[str, Any]], *, user_id: int | None = None) -> int:
        if not rows:
            return 0
        imported = 0
        with self.connect() as conn:
            for item in rows:
                league = str(item.get("league") or "").strip()
                season = item.get("season")
                if not league:
                    continue
                conn.execute(
                    """
                    INSERT INTO league_reliability_scores (
                        user_id, league, season, match_count, trainable_count, odds_count, stats_count,
                        avg_data_quality, roi_simulated, drawdown, stability_score,
                        league_reliability_score, classification, reasons_json, calculated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(league, season) DO UPDATE SET
                        match_count = excluded.match_count,
                        trainable_count = excluded.trainable_count,
                        odds_count = excluded.odds_count,
                        stats_count = excluded.stats_count,
                        avg_data_quality = excluded.avg_data_quality,
                        roi_simulated = excluded.roi_simulated,
                        drawdown = excluded.drawdown,
                        stability_score = excluded.stability_score,
                        league_reliability_score = excluded.league_reliability_score,
                        classification = excluded.classification,
                        reasons_json = excluded.reasons_json,
                        calculated_at = excluded.calculated_at
                    """,
                    (
                        user_id,
                        league,
                        season,
                        int(item.get("match_count") or 0),
                        int(item.get("trainable_count") or 0),
                        int(item.get("odds_count") or 0),
                        int(item.get("stats_count") or 0),
                        float(item.get("avg_data_quality") or 0.0),
                        float(item.get("roi_simulated") or 0.0),
                        float(item.get("drawdown") or 0.0),
                        float(item.get("stability_score") or 0.0),
                        float(item.get("league_reliability_score") or 0.0),
                        str(item.get("classification") or "Em observação"),
                        _json(_loads(item.get("reasons_json"), item.get("reasons") or [])),
                        str(item.get("calculated_at") or _now_iso()),
                    ),
                )
                imported += 1
        return imported

    def save_prediction(self, prediction: MatchPrediction, *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO predictions (
                    user_id, match_id, model_version_id, market, estimated_probability, fair_odd, offered_odd,
                    expected_value, confidence_score, risk_level, recommendation, explanation_json, created_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    prediction.match_id,
                    prediction.market,
                    prediction.estimated_probability,
                    prediction.fair_odd,
                    prediction.offered_odd,
                    prediction.expected_value,
                    prediction.confidence_score,
                    prediction.risk_level,
                    prediction.recommendation,
                    _json(prediction.explanation),
                    prediction.created_at.isoformat(),
                ),
            )
            row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO bets_analysis (
                    user_id, prediction_id, bankroll_profile, suggested_stake, kelly_fraction, no_bet_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    row_id,
                    prediction.bankroll.profile,
                    prediction.bankroll.suggested_stake,
                    prediction.bankroll.kelly_fraction,
                    None if prediction.bankroll.allowed else prediction.bankroll.reason,
                    prediction.created_at.isoformat(),
                ),
            )
        return row_id

    def list_predictions(self, *, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, b.bankroll_profile, b.suggested_stake, b.kelly_fraction, b.no_bet_reason
                FROM predictions p
                LEFT JOIN bets_analysis b ON b.prediction_id = p.id
                ORDER BY p.created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["explanation"] = _loads(item.pop("explanation_json", None), {})
            result.append(item)
        return result

    def create_model_version(self, *, name: str, rules: dict[str, Any], notes: str = "", user_id: int | None = None, status: str = "draft") -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO model_versions (user_id, name, status, rules_json, notes, created_at, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (user_id, name[:120], status[:20], _json(rules), notes[:1000], _now_iso()),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def save_simulation_run(self, summary: dict[str, Any], *, user_id: int | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO simulation_runs (
                    user_id, model_version_id, label, league, market, season, date_from, date_to,
                    initial_bankroll, final_bankroll, total_games, total_entries, hit_rate, roi, drawdown,
                    profit_loss, status, created_at
                ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    str(summary.get("label") or "Backtest"),
                    summary.get("league"),
                    summary.get("market"),
                    summary.get("season"),
                    summary.get("date_from"),
                    summary.get("date_to"),
                    summary.get("initial_bankroll"),
                    summary.get("final_bankroll"),
                    summary.get("total_games"),
                    summary.get("total_entries"),
                    summary.get("hit_rate"),
                    summary.get("roi"),
                    summary.get("drawdown_max"),
                    summary.get("profit_loss"),
                    str(summary.get("status") or "completed"),
                    _now_iso(),
                ),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def save_simulation_results(self, run_id: int, rows: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        now = _now_iso()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO simulation_results (
                        user_id, simulation_run_id, historical_match_id, prediction_id, market, offered_odd,
                        fair_odd, expected_value, stake, result, profit_loss, bankroll_after, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        run_id,
                        row.get("historical_match_id"),
                        row.get("prediction_id"),
                        row.get("market"),
                        row.get("offered_odd"),
                        row.get("fair_odd"),
                        row.get("expected_value"),
                        row.get("stake"),
                        row.get("result"),
                        row.get("profit_loss"),
                        row.get("bankroll_after"),
                        now,
                    ),
                )

    def save_model_performance_rows(self, rows: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        now = _now_iso()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO model_performance (
                        user_id, model_version_id, scope, league, market, total_entries, hit_rate, roi,
                        profit_loss, drawdown, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        row.get("model_version_id"),
                        row.get("scope"),
                        row.get("league"),
                        row.get("market"),
                        row.get("total_entries"),
                        row.get("hit_rate"),
                        row.get("roi"),
                        row.get("profit_loss"),
                        row.get("drawdown"),
                        now,
                    ),
                )

    def save_strategy_rule(self, *, name: str, version_name: str, rules: dict[str, Any], notes: str = "", status: str = "draft", user_id: int | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_rules (user_id, name, status, version_name, rules_json, notes, created_at, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (user_id, name[:160], status[:20], version_name[:80], _json(rules), notes[:1000], _now_iso()),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def save_strategy_suggestion(self, *, suggestion_type: str, title: str, description: str, payload: dict[str, Any], strategy_rule_id: int | None = None, user_id: int | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO strategy_suggestions (
                    user_id, strategy_rule_id, suggestion_type, title, description, payload_json, status, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
                """,
                (user_id, strategy_rule_id, suggestion_type[:80], title[:220], description[:2000], _json(payload), _now_iso()),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def list_strategy_suggestions(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM strategy_suggestions"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json", None), {})
            result.append(item)
        return result

    def decide_strategy_suggestion(self, suggestion_id: int, decision: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE strategy_suggestions SET status = ?, decided_at = ? WHERE id = ?",
                (decision, _now_iso(), suggestion_id),
            )
            row = conn.execute("SELECT * FROM strategy_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = _loads(item.pop("payload_json", None), {})
        return item

    def save_learning_event(self, event_type: str, payload: dict[str, Any], *, ref_type: str | None = None, ref_id: str | None = None, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO learning_events (user_id, event_type, ref_type, ref_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, event_type[:80], (ref_type or "")[:80] or None, (ref_id or "")[:120] or None, _json(payload), _now_iso()),
            )

    def save_rag_document(self, *, title: str, source_type: str, source_ref: str | None, body: str, metadata: dict[str, Any], user_id: int | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_documents (user_id, title, source_type, source_ref, body, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, title[:220], source_type[:80], source_ref, body, _json(metadata), _now_iso()),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def replace_rag_chunks(self, document_id: int, chunks: list[dict[str, Any]], *, user_id: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
            for row in chunks:
                conn.execute(
                    """
                    INSERT INTO rag_chunks (
                        user_id, document_id, chunk_index, content, tokens_estimate, search_text, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        document_id,
                        row.get("chunk_index"),
                        row.get("content"),
                        row.get("tokens_estimate", 0),
                        row.get("search_text"),
                        _json(row.get("metadata", {})),
                        _now_iso(),
                    ),
                )

    def search_rag_chunks(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        terms = [term.strip().lower() for term in query.split() if len(term.strip()) >= 3]
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, d.title, d.source_type, d.source_ref
                FROM rag_chunks c
                JOIN rag_documents d ON d.id = c.document_id
                ORDER BY c.created_at DESC
                """
            ).fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            search_text = str(row["search_text"] or "").lower()
            score = sum(3 if term in search_text else 0 for term in terms)
            if score <= 0 and terms:
                continue
            item = dict(row)
            item["metadata"] = _loads(item.pop("metadata_json", None), {})
            scored.append((score, item))
        scored.sort(key=lambda item: (item[0], item[1].get("chunk_index", 0)), reverse=True)
        return [item for _, item in scored[:limit]]

    def system_snapshot(self) -> dict[str, Any]:
        tables = [
            "data_sources",
            "historical_matches",
            "historical_stats",
            "historical_odds",
            "historical_features",
            "league_reliability_scores",
            "historical_import_batches",
            "predictions",
            "simulation_runs",
            "simulation_results",
            "strategy_suggestions",
            "rag_documents",
            "rag_chunks",
            "historical_corners",
            "historical_cards",
            "historical_asian_lines",
            "market_pressure_snapshots",
            "referee_profiles",
            "live_market_movements",
            "football_research_logs",
        ]
        counts: dict[str, int] = {}
        with self.connect() as conn:
            for table in tables:
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "db_file": self.path.as_posix(),
            "db_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "counts": counts,
        }

    def discovery_report(self) -> dict[str, Any]:
        with self.connect() as conn:
            tables = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()]
            columns: dict[str, list[str]] = {}
            for table in tables:
                columns[table] = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        snapshot = self.system_snapshot()
        return {
            "tables": tables,
            "columns": columns,
            "counts": snapshot["counts"],
            "db_file": snapshot["db_file"],
            "db_size_bytes": snapshot["db_size_bytes"],
        }

    def aggregate_simulation_performance(self) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.market, m.league, s.offered_odd, s.expected_value, s.result, s.profit_loss
                FROM simulation_results s
                JOIN historical_matches m ON m.id = s.historical_match_id
                LEFT JOIN simulation_runs r ON r.id = s.simulation_run_id
                """
            ).fetchall()
        by_league: dict[str, list[float]] = defaultdict(list)
        by_market: dict[str, list[float]] = defaultdict(list)
        by_odds: dict[str, list[float]] = defaultdict(list)
        by_ev: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            profit = float(row["profit_loss"] or 0)
            league = str(row["league"] or "Sem liga")
            market = str(row["market"] or "misc")
            odd = float(row["offered_odd"] or 0)
            ev = float(row["expected_value"] or 0)
            by_league[league].append(profit)
            by_market[market].append(profit)
            by_odds[_odds_band(odd)].append(profit)
            by_ev[_ev_band(ev)].append(profit)
        return {
            "by_league": _aggregate_profit_groups(by_league),
            "by_market": _aggregate_profit_groups(by_market),
            "by_odds": _aggregate_profit_groups(by_odds),
            "by_ev": _aggregate_profit_groups(by_ev),
        }


def _aggregate_profit_groups(groups: dict[str, list[float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, items in groups.items():
        total = len(items)
        profit = round(sum(items), 2)
        wins = sum(1 for item in items if item > 0)
        rows.append(
            {
                "name": name,
                "total": total,
                "profit_loss": profit,
                "hit_rate": round((wins / total) * 100, 2) if total else 0.0,
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
        return "sem_valor"
    if ev < 0.03:
        return "baixo"
    if ev < 0.08:
        return "moderado"
    return "alto"


def _historical_data_quality(payload: dict[str, Any]) -> int:
    score = 0
    status = str(payload.get("status") or "").upper()
    if status in {"FT", "AET", "PEN", "FINISHED"} and payload.get("home_goals") is not None and payload.get("away_goals") is not None:
        score += 25
    odds = payload.get("odds") or []
    if isinstance(odds, list) and any(str(item.get("source") or "").lower().find("mock") < 0 for item in odds if isinstance(item, dict)):
        score += 25
    stats = payload.get("stats") or {}
    if isinstance(stats, dict) and any(value not in (None, "") for value in stats.values()):
        score += 20
    if payload.get("league") and payload.get("home_team") and payload.get("away_team"):
        score += 15
    if payload.get("external_id") or (payload.get("league") and payload.get("match_date") and payload.get("home_team") and payload.get("away_team")):
        score += 15
    return min(100, score)


def _historical_duplicate_key(
    *,
    source: str,
    external_id: str,
    league: str,
    season: int | None,
    match_date: str,
    home_team: str,
    away_team: str,
) -> str:
    if external_id:
        return f"{source}:{external_id}".lower()
    parts = [source, league, str(season or ""), match_date[:19], home_team, away_team]
    return ":".join(_normalize_key_part(part) for part in parts)


def _normalize_key_part(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
