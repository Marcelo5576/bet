from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any

from services.markets import build_market_intelligence


_BRAIN_CACHE: dict[str, "FootballBrain"] = {}
_BRAIN_LOCK = threading.Lock()


def get_football_brain(settings) -> "FootballBrain | None":
    if not bool(getattr(settings, "brain_enabled", False)):
        return None
    db_file = str(getattr(settings, "brain_db_file", "data/football_brain.db") or "data/football_brain.db")
    resolved = str(Path(db_file).resolve())
    with _BRAIN_LOCK:
        brain = _BRAIN_CACHE.get(resolved)
        if brain is None:
            brain = FootballBrain(resolved)
            _BRAIN_CACHE[resolved] = brain
        return brain


class FootballBrain:
    def __init__(self, db_file: str):
        self.db_file = str(db_file)
        self._write_lock = threading.Lock()
        path = Path(self.db_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def record_pregame_watchlist(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for item in entries:
                game_id = str(item.get("game_id") or "").strip()
                if not game_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO brain_pregame_watchlist (
                        game_id,
                        recorded_at,
                        kickoff_at,
                        league,
                        home_team,
                        away_team,
                        promising_score,
                        focus,
                        starts_in_minutes,
                        home_price,
                        draw_price,
                        away_price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_id,
                        now,
                        _as_text(item.get("kickoff_at")),
                        _as_text(item.get("league")),
                        _as_text(item.get("home")),
                        _as_text(item.get("away")),
                        _safe_int(item.get("promising_score")),
                        _as_text(item.get("focus")),
                        _safe_int(item.get("starts_in_minutes")),
                        _safe_float(item.get("home_price"), None),
                        _safe_float(item.get("draw_price"), None),
                        _safe_float(item.get("away_price"), None),
                    ),
                )
            conn.commit()

    def record_live_games(self, games: list[Any], source: str = "") -> None:
        if not games:
            return
        captured_at = datetime.now(timezone.utc).isoformat()
        with self._write_lock:
            with self._connect() as conn:
                touched_match_ids: set[str] = set()
                for raw_game in games:
                    game = _to_dict(raw_game)
                    match_id = str(game.get("game_id") or "").strip()
                    if not match_id:
                        continue
                    touched_match_ids.add(match_id)
                    facts = _extract_live_facts(game)
                    conn.execute(
                        """
                        INSERT INTO brain_matches (
                            match_id, source, league, season, kickoff_at,
                            home_team, away_team, last_status, last_minute, last_seen_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(match_id) DO UPDATE SET
                            source=excluded.source,
                            league=excluded.league,
                            season=excluded.season,
                            kickoff_at=excluded.kickoff_at,
                            home_team=excluded.home_team,
                            away_team=excluded.away_team,
                            last_status=excluded.last_status,
                            last_minute=excluded.last_minute,
                            last_seen_at=excluded.last_seen_at
                        """,
                        (
                            match_id,
                            _as_text(source),
                            _as_text(game.get("league") or game.get("division")),
                            _season_from_kickoff(game.get("kickoff_at")),
                            _as_text(game.get("kickoff_at")),
                            _as_text(game.get("home")),
                            _as_text(game.get("away")),
                            _as_text(game.get("status") or game.get("state")),
                            _safe_int(game.get("minute")),
                            captured_at,
                        ),
                    )
                    last = conn.execute(
                        """
                        SELECT minute, home_goals, away_goals, captured_at
                        FROM brain_live_snapshots
                        WHERE match_id = ?
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (match_id,),
                    ).fetchone()
                    same_snapshot = False
                    if last:
                        same_snapshot = (
                            _safe_int(last["minute"]) == _safe_int(game.get("minute"))
                            and _safe_int(last["home_goals"]) == _safe_int(game.get("home_goals"))
                            and _safe_int(last["away_goals"]) == _safe_int(game.get("away_goals"))
                            and _age_seconds(last["captured_at"]) < 75
                        )
                    if same_snapshot:
                        continue
                    goal_market = _market_totals(game, "goals")
                    conn.execute(
                        """
                        INSERT INTO brain_live_snapshots (
                            match_id, captured_at, minute, home_goals, away_goals,
                            home_pressure, away_pressure, home_shots_on, away_shots_on,
                            possession_home, possession_away, shots_home, shots_away,
                            corners_home, corners_away, yellow_home, yellow_away,
                            red_home, red_away, dangerous_attacks_home, dangerous_attacks_away,
                            attacks_home, attacks_away, xg_home, xg_away,
                            odds_home, odds_draw, odds_away,
                            over_line, over_odd, under_odd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            match_id,
                            captured_at,
                            _safe_int(game.get("minute")),
                            _safe_int(game.get("home_goals")),
                            _safe_int(game.get("away_goals")),
                            _safe_int(game.get("home_pressure")),
                            _safe_int(game.get("away_pressure")),
                            _safe_int(game.get("home_shots_on")),
                            _safe_int(game.get("away_shots_on")),
                            _safe_float(facts.get("possession_home"), 50.0),
                            _safe_float(facts.get("possession_away"), 50.0),
                            _safe_int(facts.get("shots_home")),
                            _safe_int(facts.get("shots_away")),
                            _safe_int(facts.get("corners_home")),
                            _safe_int(facts.get("corners_away")),
                            _safe_int(facts.get("yellow_home")),
                            _safe_int(facts.get("yellow_away")),
                            _safe_int(facts.get("red_home")),
                            _safe_int(facts.get("red_away")),
                            _safe_int(facts.get("dangerous_attacks_home")),
                            _safe_int(facts.get("dangerous_attacks_away")),
                            _safe_int(facts.get("attacks_home")),
                            _safe_int(facts.get("attacks_away")),
                            _safe_float(facts.get("xg_home"), None),
                            _safe_float(facts.get("xg_away"), None),
                            _safe_float(game.get("odds_home"), None),
                            _safe_float(game.get("odds_draw"), None),
                            _safe_float(game.get("odds_away"), None),
                            _as_text(goal_market.get("line")),
                            _safe_float(goal_market.get("over_odd"), None),
                            _safe_float(goal_market.get("under_odd"), None),
                        ),
                    )
                for match_id in touched_match_ids:
                    self._harvest_learning_events(conn, match_id)
                conn.commit()

    def enrich_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        game = _to_dict(signal.get("game"))
        match_id = str(game.get("game_id") or "").strip()
        if not match_id:
            signal["brain"] = {"enabled": True, "status": "sem match_id", "skills": []}
            return signal

        with self._connect() as conn:
            snapshots = conn.execute(
                """
                SELECT *
                FROM brain_live_snapshots
                WHERE match_id = ?
                ORDER BY id DESC
                LIMIT 8
                """,
                (match_id,),
            ).fetchall()
            pregame = conn.execute(
                """
                SELECT *
                FROM brain_pregame_watchlist
                WHERE game_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (match_id,),
            ).fetchone()

        facts = _extract_live_facts(game)
        features = _build_live_features(game, facts, snapshots)
        skills = _evaluate_skills(signal, game, facts, features, pregame)
        market_intelligence = build_market_intelligence(game)
        market_skills = _market_intelligence_skills(market_intelligence)
        if market_skills:
            skills.extend(market_skills)
        _merge_market_recommendations(signal, market_intelligence.get("recommendations") or [])
        best_skill = _best_skill(skills)
        live_sample = len(snapshots)
        if skills:
            captured_at = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                for item in skills:
                    conn.execute(
                        """
                        INSERT INTO brain_skill_results (
                            match_id, captured_at, skill_name, decision, confidence, market, edge, reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            match_id,
                            captured_at,
                            _as_text(item.get("name")),
                            _as_text(item.get("decision")),
                            _safe_float(item.get("confidence"), 0.0),
                            _as_text(item.get("market")),
                            _safe_float(item.get("edge"), None),
                            _as_text(item.get("reason")),
                        ),
                    )
                conn.commit()
        brain_payload = {
            "enabled": True,
            "match_id": match_id,
            "sample_size": live_sample,
            "pressure_index_home": round(features["pressure_index_home"], 2),
            "pressure_index_away": round(features["pressure_index_away"], 2),
            "momentum_score": round(features["momentum_score"], 2),
            "pressure_trend": round(features["pressure_trend"], 2),
            "risk_score": round(features["risk_score"], 2),
            "data_quality": round(features["data_quality"], 2),
            "facts": facts,
            "best_skill": best_skill,
            "skills": skills,
            "market_intelligence": market_intelligence,
            "pregame_context": _pregame_summary(pregame),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        signal["brain"] = brain_payload

        if best_skill:
            signal["brain_reason"] = str(best_skill.get("reason") or "")
            signal["brain_market"] = str(best_skill.get("market") or "")
            signal["brain_decision"] = str(best_skill.get("decision") or "")
            signal["brain_confidence"] = _safe_int(best_skill.get("confidence"))
            signal["brain_edge"] = _safe_float(best_skill.get("edge"), 0.0)
            signal["brain_score"] = max(
                _safe_int(signal.get("entry_score")),
                min(99, _safe_int(best_skill.get("confidence"))),
            )
            signal["data_quality"] = max(
                _safe_int(signal.get("data_quality")),
                min(100, _safe_int(brain_payload.get("data_quality"))),
            )
            base_reason = str(signal.get("reason") or "").strip()
            if signal["brain_reason"] and signal["brain_reason"] not in base_reason:
                signal["reason"] = (
                    f"{base_reason} | Brain: {signal['brain_reason']}"
                    if base_reason
                    else f"Brain: {signal['brain_reason']}"
                )

            decision = str(best_skill.get("decision") or "").upper()
            confidence = _safe_int(best_skill.get("confidence"))
            edge = _safe_float(best_skill.get("edge"), 0.0)
            if (
                decision == "ENTRA"
                and edge > 0
                and confidence >= 74
                and _safe_float(brain_payload.get("risk_score"), 100.0) <= 62
                and not signal.get("risk_blocked")
                and str(signal.get("action") or "").upper() != "SAIR"
            ):
                if str(signal.get("action") or "").upper() != "ENTRAR":
                    signal["action"] = "ENTRAR"
                signal["confidence"] = max(_safe_int(signal.get("confidence")), confidence)
                if signal["brain_market"]:
                    signal["market"] = signal["brain_market"]
                if best_skill.get("odd"):
                    signal["target_odds"] = best_skill.get("odd")
            elif (
                decision in {"SAI", "NAO_ENTRAR"}
                and str(signal.get("action") or "").upper() == "ENTRAR"
                and _safe_float(brain_payload.get("risk_score"), 0.0) >= 82
            ):
                signal["action"] = "AGUARDAR"

            signal["score_note"] = _brain_score_note(signal)

        return signal

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM brain_matches) AS matches_count,
                    (SELECT COUNT(*) FROM brain_live_snapshots) AS snapshots_count,
                    (SELECT COUNT(*) FROM brain_pregame_watchlist) AS pregame_rows,
                    (SELECT COUNT(*) FROM brain_skill_results) AS skill_rows,
                    (SELECT COUNT(*) FROM brain_learning_events) AS learning_events,
                    (SELECT MAX(captured_at) FROM brain_live_snapshots) AS last_snapshot_at
                """
            ).fetchone()
            learning_summary = conn.execute(
                """
                SELECT
                    market,
                    COUNT(*) AS total,
                    SUM(CASE WHEN label = 'green' THEN 1 ELSE 0 END) AS greens,
                    SUM(CASE WHEN label = 'red' THEN 1 ELSE 0 END) AS reds,
                    ROUND(AVG(confidence_before), 1) AS avg_confidence,
                    ROUND(AVG(score_before), 1) AS avg_score
                FROM brain_learning_events
                GROUP BY market
                ORDER BY total DESC
                LIMIT 6
                """
            ).fetchall()
        return {
            "enabled": True,
            "db_file": self.db_file,
            "matches": _safe_int(counts["matches_count"]),
            "live_snapshots": _safe_int(counts["snapshots_count"]),
            "pregame_rows": _safe_int(counts["pregame_rows"]),
            "skill_rows": _safe_int(counts["skill_rows"]),
            "learning_events": _safe_int(counts["learning_events"]),
            "learning_summary": [
                {
                    "market": row["market"],
                    "total": _safe_int(row["total"]),
                    "greens": _safe_int(row["greens"]),
                    "reds": _safe_int(row["reds"]),
                    "green_rate": round((_safe_int(row["greens"]) / max(1, _safe_int(row["total"]))) * 100, 1),
                    "avg_confidence": _safe_float(row["avg_confidence"], 0.0),
                    "avg_score": _safe_float(row["avg_score"], 0.0),
                }
                for row in learning_summary
            ],
            "last_snapshot_at": counts["last_snapshot_at"],
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS brain_matches (
                    match_id TEXT PRIMARY KEY,
                    source TEXT,
                    league TEXT,
                    season INTEGER,
                    kickoff_at TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    last_status TEXT,
                    last_minute INTEGER,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS brain_live_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    minute INTEGER,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    home_pressure INTEGER,
                    away_pressure INTEGER,
                    home_shots_on INTEGER,
                    away_shots_on INTEGER,
                    possession_home REAL,
                    possession_away REAL,
                    shots_home INTEGER,
                    shots_away INTEGER,
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
                    odds_home REAL,
                    odds_draw REAL,
                    odds_away REAL,
                    over_line TEXT,
                    over_odd REAL,
                    under_odd REAL
                );
                CREATE INDEX IF NOT EXISTS idx_brain_snapshots_match_time
                ON brain_live_snapshots(match_id, captured_at DESC);

                CREATE TABLE IF NOT EXISTS brain_pregame_watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    kickoff_at TEXT,
                    league TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    promising_score INTEGER,
                    focus TEXT,
                    starts_in_minutes INTEGER,
                    home_price REAL,
                    draw_price REAL,
                    away_price REAL
                );
                CREATE INDEX IF NOT EXISTS idx_brain_pregame_game
                ON brain_pregame_watchlist(game_id, recorded_at DESC);

                CREATE TABLE IF NOT EXISTS brain_skill_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    decision TEXT,
                    confidence REAL,
                    market TEXT,
                    edge REAL,
                    reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_brain_skill_results_match
                ON brain_skill_results(match_id, captured_at DESC);

                CREATE TABLE IF NOT EXISTS brain_learning_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_snapshot_id INTEGER NOT NULL,
                    resolved_snapshot_id INTEGER NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    market TEXT NOT NULL,
                    label TEXT NOT NULL,
                    confidence_before REAL,
                    score_before REAL,
                    odd_before REAL,
                    minute_before INTEGER,
                    minute_after INTEGER,
                    scoreline_before TEXT,
                    scoreline_after TEXT,
                    reason TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_brain_learning_unique
                ON brain_learning_events(source_snapshot_id, horizon_minutes, market);
                CREATE INDEX IF NOT EXISTS idx_brain_learning_match
                ON brain_learning_events(match_id, created_at DESC);
                """
            )
            conn.commit()

    def _harvest_learning_events(self, conn: sqlite3.Connection, match_id: str) -> None:
        sources = conn.execute(
            """
            SELECT s.*
            FROM brain_live_snapshots s
            WHERE s.match_id = ?
              AND COALESCE(s.minute, 0) BETWEEN 5 AND 84
              AND NOT EXISTS (
                  SELECT 1
                  FROM brain_learning_events e
                  WHERE e.source_snapshot_id = s.id
                    AND e.horizon_minutes = 10
                    AND e.market = 'GOAL_NEXT_10'
              )
            ORDER BY s.id DESC
            LIMIT 80
            """,
            (match_id,),
        ).fetchall()
        for source in sources:
            event = _resolve_goal_next_10_event(conn, source)
            if not event:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO brain_learning_events (
                    match_id, created_at, source_snapshot_id, resolved_snapshot_id,
                    horizon_minutes, market, label, confidence_before, score_before,
                    odd_before, minute_before, minute_after, scoreline_before,
                    scoreline_after, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    datetime.now(timezone.utc).isoformat(),
                    int(source["id"]),
                    int(event["resolved_snapshot_id"]),
                    10,
                    "GOAL_NEXT_10",
                    event["label"],
                    event["confidence_before"],
                    event["score_before"],
                    event["odd_before"],
                    _safe_int(source["minute"]),
                    event["minute_after"],
                    event["scoreline_before"],
                    event["scoreline_after"],
                    event["reason"],
                ),
            )


def _resolve_goal_next_10_event(conn: sqlite3.Connection, source: sqlite3.Row) -> dict[str, Any] | None:
    source_minute = _safe_int(source["minute"])
    if source_minute <= 0 or source_minute >= 85:
        return None
    source_total_goals = _safe_int(source["home_goals"]) + _safe_int(source["away_goals"])
    future_rows = conn.execute(
        """
        SELECT *
        FROM brain_live_snapshots
        WHERE match_id = ?
          AND id > ?
          AND COALESCE(minute, 0) >= ?
        ORDER BY minute ASC, id ASC
        LIMIT 80
        """,
        (source["match_id"], int(source["id"]), source_minute),
    ).fetchall()
    if not future_rows:
        return None

    first_goal_row = None
    latest_row = future_rows[-1]
    for row in future_rows:
        row_total_goals = _safe_int(row["home_goals"]) + _safe_int(row["away_goals"])
        if row_total_goals > source_total_goals:
            first_goal_row = row
            break

    if first_goal_row is not None:
        minute_after = _safe_int(first_goal_row["minute"])
        if minute_after - source_minute <= 12:
            return _learning_event_payload(
                source,
                first_goal_row,
                label="green",
                reason="Gol real apareceu dentro da janela de 10 minutos com tolerancia operacional.",
            )

    latest_minute = _safe_int(latest_row["minute"])
    if latest_minute - source_minute >= 10:
        return _learning_event_payload(
            source,
            latest_row,
            label="red",
            reason="Janela de 10 minutos fechou sem novo gol real.",
        )
    return None


def _learning_event_payload(
    source: sqlite3.Row,
    resolved: sqlite3.Row,
    *,
    label: str,
    reason: str,
) -> dict[str, Any]:
    confidence = _snapshot_goal_pressure_confidence(source)
    score = _snapshot_goal_pressure_score(source)
    return {
        "resolved_snapshot_id": int(resolved["id"]),
        "label": label,
        "confidence_before": confidence,
        "score_before": score,
        "odd_before": _safe_float(source["over_odd"], None),
        "minute_after": _safe_int(resolved["minute"]),
        "scoreline_before": _snapshot_scoreline(source),
        "scoreline_after": _snapshot_scoreline(resolved),
        "reason": reason,
    }


def _snapshot_goal_pressure_confidence(row: sqlite3.Row) -> float:
    shots_on_total = _safe_float(row["home_shots_on"]) + _safe_float(row["away_shots_on"])
    pressure_total = _safe_float(row["home_pressure"]) + _safe_float(row["away_pressure"])
    dangerous_total = _safe_float(row["dangerous_attacks_home"]) + _safe_float(row["dangerous_attacks_away"])
    corners_total = _safe_float(row["corners_home"]) + _safe_float(row["corners_away"])
    red_total = _safe_float(row["red_home"]) + _safe_float(row["red_away"])
    minute = _safe_int(row["minute"])
    late_penalty = 8 if minute >= 78 else 0
    confidence = 32 + shots_on_total * 7 + pressure_total * 0.16 + dangerous_total * 0.22 + corners_total * 2.2
    confidence -= red_total * 8 + late_penalty
    return round(max(5.0, min(95.0, confidence)), 2)


def _snapshot_goal_pressure_score(row: sqlite3.Row) -> float:
    home_pressure = _safe_float(row["home_pressure"])
    away_pressure = _safe_float(row["away_pressure"])
    shots_on_total = _safe_float(row["home_shots_on"]) + _safe_float(row["away_shots_on"])
    shots_total = _safe_float(row["shots_home"]) + _safe_float(row["shots_away"])
    corners_total = _safe_float(row["corners_home"]) + _safe_float(row["corners_away"])
    dangerous_total = _safe_float(row["dangerous_attacks_home"]) + _safe_float(row["dangerous_attacks_away"])
    score = 25 + max(home_pressure, away_pressure) * 0.28 + shots_on_total * 7 + shots_total * 1.5
    score += corners_total * 2.0 + dangerous_total * 0.18
    return round(max(1.0, min(100.0, score)), 2)


def _snapshot_scoreline(row: sqlite3.Row) -> str:
    return f"{_safe_int(row['home_goals'])}x{_safe_int(row['away_goals'])}"


def _build_live_features(game: dict[str, Any], facts: dict[str, Any], snapshots: list[sqlite3.Row]) -> dict[str, float]:
    home_shots_on = _safe_float(facts.get("shots_on_home"), _safe_float(game.get("home_shots_on"), 0.0))
    away_shots_on = _safe_float(facts.get("shots_on_away"), _safe_float(game.get("away_shots_on"), 0.0))
    home_shots = _safe_float(facts.get("shots_home"), home_shots_on + 1.0)
    away_shots = _safe_float(facts.get("shots_away"), away_shots_on + 1.0)
    home_poss = _safe_float(facts.get("possession_home"), 50.0)
    away_poss = _safe_float(facts.get("possession_away"), 50.0)
    home_corners = _safe_float(facts.get("corners_home"), 0.0)
    away_corners = _safe_float(facts.get("corners_away"), 0.0)
    home_danger = _safe_float(facts.get("dangerous_attacks_home"), max(_safe_float(game.get("home_pressure"), 0.0) * 0.5, 0.0))
    away_danger = _safe_float(facts.get("dangerous_attacks_away"), max(_safe_float(game.get("away_pressure"), 0.0) * 0.5, 0.0))
    pressure_index_home = (
        home_shots_on * 2.5
        + home_shots * 1.2
        + home_danger * 0.8
        + home_corners * 1.0
        + home_poss * 0.15
    )
    pressure_index_away = (
        away_shots_on * 2.5
        + away_shots * 1.2
        + away_danger * 0.8
        + away_corners * 1.0
        + away_poss * 0.15
    )
    momentum_score = pressure_index_home - pressure_index_away
    pressure_trend = 0.0
    if len(snapshots) >= 2:
        oldest = snapshots[-1]
        oldest_momentum = (
            _safe_float(oldest["home_pressure"]) - _safe_float(oldest["away_pressure"])
            + (_safe_float(oldest["home_shots_on"]) - _safe_float(oldest["away_shots_on"])) * 4
        )
        pressure_trend = momentum_score - oldest_momentum

    minute = _safe_int(game.get("minute"))
    red_total = _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away"))
    shots_on_total = home_shots_on + away_shots_on
    risk_score = 28.0
    if len(snapshots) < 3:
        risk_score += 18
    if minute < 8:
        risk_score += 9
    if minute > 78:
        risk_score += 16
    if shots_on_total <= 2:
        risk_score += 10
    if red_total:
        risk_score += 10 + (red_total * 5)
    goal_market = _market_totals(game, "goals")
    over_odd = _safe_float(goal_market.get("over_odd"), 0.0)
    if 0 < over_odd < 1.3:
        risk_score += 8
    data_quality = 40.0
    for key in ("shots_home", "shots_away", "possession_home", "possession_away", "corners_home", "corners_away"):
        if facts.get(key) is not None:
            data_quality += 8
    if facts.get("dangerous_attacks_home") is not None and facts.get("dangerous_attacks_away") is not None:
        data_quality += 10
    if goal_market.get("over_odd") is not None or goal_market.get("under_odd") is not None:
        data_quality += 8
    if len(snapshots) >= 2:
        data_quality += min(16, len(snapshots) * 2)
    return {
        "pressure_index_home": pressure_index_home,
        "pressure_index_away": pressure_index_away,
        "momentum_score": momentum_score,
        "pressure_trend": pressure_trend,
        "risk_score": max(0.0, min(100.0, risk_score)),
        "data_quality": max(0.0, min(100.0, data_quality)),
    }


def _evaluate_skills(
    signal: dict[str, Any],
    game: dict[str, Any],
    facts: dict[str, Any],
    features: dict[str, float],
    pregame: sqlite3.Row | None,
) -> list[dict[str, Any]]:
    skills = [
        _skill_over15_prelive(game, features, pregame),
        _skill_over25_controlado(game, facts, features),
        _skill_btts(game, facts, features),
        _skill_corners_live(game, facts, features),
        _skill_sair_segurar(signal, game, facts, features),
    ]
    actionable = [item for item in skills if item]
    ev_skill = _skill_ev_value(actionable)
    if ev_skill:
        actionable.append(ev_skill)
    return actionable


def _skill_over15_prelive(game: dict[str, Any], features: dict[str, float], pregame: sqlite3.Row | None) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    if minute > 18:
        return {}
    goals_total = _safe_int(game.get("home_goals")) + _safe_int(game.get("away_goals"))
    market = _market_totals(game, "goals")
    focus = str(pregame["focus"]) if pregame and pregame["focus"] else "gols"
    score = _safe_int(pregame["promising_score"]) if pregame else 0
    over_line = _as_text(market.get("line")) or "1.5+"
    over_odd = _safe_float(market.get("over_odd"), None)
    confidence = min(88, 48 + score // 2 + max(0, 12 - minute))
    decision = "MONITORAR"
    reason = f"Watchlist pre-jogo marcou {score}/100 com foco em {focus}."
    if score >= 72 and goals_total <= 1 and minute <= 12 and (over_odd or 0) >= 1.35:
        decision = "ENTRA"
        confidence = min(92, confidence + 8)
        reason = "Grade promissora abriu ao vivo cedo e manteve contexto limpo para over inicial."
    return {
        "name": "SKILL_OVER_15_PRELIVE",
        "market": "Gols",
        "selection": f"Over {over_line}",
        "decision": decision,
        "confidence": confidence,
        "odd": over_odd,
        "edge": None,
        "reason": reason,
    }


def _skill_over25_controlado(game: dict[str, Any], facts: dict[str, Any], features: dict[str, float]) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    if minute < 12 or minute > 76:
        return {}
    goals_total = _safe_int(game.get("home_goals")) + _safe_int(game.get("away_goals"))
    shots_on_total = _safe_int(facts.get("shots_on_home"), _safe_int(game.get("home_shots_on"))) + _safe_int(
        facts.get("shots_on_away"), _safe_int(game.get("away_shots_on"))
    )
    dangerous_total = _safe_int(facts.get("dangerous_attacks_home")) + _safe_int(facts.get("dangerous_attacks_away"))
    market = _market_totals(game, "goals")
    over_odd = _safe_float(market.get("over_odd"), None)
    probability = 0.36 + min(0.36, shots_on_total * 0.035 + dangerous_total * 0.006)
    probability += min(0.08, max(features["momentum_score"], 0.0) * 0.0018)
    probability -= min(0.12, features["risk_score"] * 0.0012)
    probability += 0.06 if goals_total >= 1 else 0.0
    confidence = max(42, min(93, int(round(probability * 100))))
    decision = "MONITORAR"
    reason = "Volume ofensivo subiu, mas ainda sem combinação ideal de ritmo e preço."
    if shots_on_total >= 4 and dangerous_total >= 18 and features["risk_score"] <= 58 and confidence >= 72:
        decision = "ENTRA"
        reason = "Shots on target, ataques perigosos e ritmo sustentam over controlado."
    return {
        "name": "SKILL_OVER_25_RISCO_CONTROLADO",
        "market": "Gols",
        "selection": f"Over {_as_text(market.get('line')) or '2.5'}",
        "decision": decision,
        "confidence": confidence,
        "odd": over_odd,
        "edge": _edge_from_probability(probability, over_odd),
        "reason": reason,
    }


def _skill_btts(game: dict[str, Any], facts: dict[str, Any], features: dict[str, float]) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    if minute < 18 or minute > 78:
        return {}
    home_sot = _safe_int(facts.get("shots_on_home"), _safe_int(game.get("home_shots_on")))
    away_sot = _safe_int(facts.get("shots_on_away"), _safe_int(game.get("away_shots_on")))
    home_pressure = _safe_int(game.get("home_pressure"))
    away_pressure = _safe_int(game.get("away_pressure"))
    home_goals = _safe_int(game.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals"))
    active = home_sot >= 1 and away_sot >= 1 and home_pressure >= 40 and away_pressure >= 40
    confidence = min(90, 46 + home_sot * 8 + away_sot * 8 + (home_pressure + away_pressure) // 10)
    if home_goals and away_goals:
        confidence = min(96, confidence + 12)
    if home_goals >= 2 and away_sot == 0:
        confidence -= 14
    if away_goals >= 2 and home_sot == 0:
        confidence -= 14
    return {
        "name": "SKILL_BTTS",
        "market": "BTTS",
        "selection": "Ambos marcam",
        "decision": "ENTRA" if active and confidence >= 70 and features["risk_score"] <= 64 else "MONITORAR",
        "confidence": max(35, min(94, confidence)),
        "odd": None,
        "edge": None,
        "reason": "Ambos os lados seguem produzindo e aceitando jogo." if active else "Um dos lados ainda produz pouco para BTTS.",
    }


def _skill_corners_live(game: dict[str, Any], facts: dict[str, Any], features: dict[str, float]) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    if minute < 15 or minute > 88:
        return {}
    corners_home = _safe_int(facts.get("corners_home"))
    corners_away = _safe_int(facts.get("corners_away"))
    corners_total = corners_home + corners_away
    pressure_total = _safe_int(game.get("home_pressure")) + _safe_int(game.get("away_pressure"))
    dangerous_total = _safe_int(facts.get("dangerous_attacks_home")) + _safe_int(facts.get("dangerous_attacks_away"))
    market = _market_totals(game, "corners")
    over_odd = _safe_float(market.get("over_odd"), None)
    has_confirmed_odd = bool(over_odd and over_odd > 1)
    line = _as_text(market.get("line")) or "linha aberta"
    probability = 0.32 + min(0.28, corners_total * 0.035 + dangerous_total * 0.004)
    probability += min(0.08, pressure_total * 0.0012)
    confidence = max(40, min(92, int(round(probability * 100))))
    active = (corners_total >= 4 and minute >= 20) or (dangerous_total >= 16 and pressure_total >= 105)
    decision = "ENTRA" if has_confirmed_odd and active and confidence >= 70 and features["risk_score"] <= 66 else "MONITORAR"
    if not has_confirmed_odd:
        reason = "Mercado de escanteios em leitura, mas sem odd real confirmada para liberar entrada."
    elif active:
        reason = "Escanteios e pressão lateral sustentam leitura de corners."
    else:
        reason = "Mercado de corners vivo, mas ainda sem aceleração suficiente."
    return {
        "name": "SKILL_CORNERS_LIVE",
        "market": "Escanteios",
        "selection": f"Over {line}",
        "decision": decision,
        "confidence": confidence,
        "odd": over_odd,
        "edge": _edge_from_probability(probability, over_odd),
        "reason": reason,
    }


def _skill_sair_segurar(
    signal: dict[str, Any],
    game: dict[str, Any],
    facts: dict[str, Any],
    features: dict[str, float],
) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    red_total = _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away"))
    current_action = str(signal.get("action") or "").upper()
    trend = features["pressure_trend"]
    momentum = features["momentum_score"]
    risk = features["risk_score"]
    decision = "SEGURA"
    reason = "Fluxo ainda respirando bem para manter monitoramento."
    if red_total and current_action == "ENTRAR":
        decision = "SAI"
        reason = "Cartão vermelho alterou o contexto do jogo."
    elif minute >= 78 and risk >= 70:
        decision = "SAI"
        reason = "Minuto avançado e risco alto para sustentar entrada nova."
    elif trend < -12 and momentum < 0:
        decision = "NAO_ENTRAR"
        reason = "Momentum esfriou e a pressão virou contra a leitura."
    elif current_action == "AGUARDAR" and trend > 8 and risk <= 60:
        decision = "SEGURA"
        reason = "Scanner pode segurar e esperar mais um ajuste de preço/pressão."
    return {
        "name": "SKILL_SAIR_SEGURAR",
        "market": str(signal.get("market") or "Gestao"),
        "selection": str(signal.get("team") or signal.get("market") or "-"),
        "decision": decision,
        "confidence": max(45, min(90, int(round(72 - risk * 0.25 + abs(trend) * 0.8)))),
        "odd": _safe_float(signal.get("target_odds"), None),
        "edge": None,
        "reason": reason,
    }


def _skill_ev_value(skills: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = None
    for item in skills:
        if str(item.get("decision") or "").upper() != "ENTRA":
            continue
        odd = _safe_float(item.get("odd"), None)
        if odd and odd > 1:
            if candidate is None or _safe_float(item.get("edge"), -999.0) > _safe_float(candidate.get("edge"), -999.0):
                candidate = item
    if not candidate:
        return {}
    probability = _safe_int(candidate.get("confidence")) / 100
    odd = _safe_float(candidate.get("odd"), 0.0)
    implied = 1 / odd if odd > 1 else 0.0
    edge = probability - implied
    decision = "ENTRA" if edge > 0 else "NAO_ENTRAR"
    return {
        "name": "SKILL_EV_VALUE",
        "market": str(candidate.get("market") or "Valor esperado"),
        "selection": str(candidate.get("selection") or "-"),
        "decision": decision,
        "confidence": candidate.get("confidence"),
        "odd": odd,
        "edge": round(edge, 4),
        "reason": (
            f"Edge positivo de {round(edge * 100, 2)}pp entre probabilidade do modelo e odd implicita."
            if edge > 0
            else "Sem edge positivo. Melhor evitar entrada agora."
        ),
    }


def _market_intelligence_skills(market_intelligence: dict[str, Any]) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for rec in market_intelligence.get("recommendations") or []:
        if not isinstance(rec, dict):
            continue
        confirmed = bool(rec.get("confirmed"))
        odd = _safe_float(rec.get("odds"), None)
        confidence = _safe_int(rec.get("confidence"))
        edge = _edge_from_probability(confidence / 100, odd) if odd and odd > 1 else None
        action = str(rec.get("action") or "MONITORAR").upper()
        decision = "MONITORAR"
        reason = str(rec.get("reason") or "").strip()
        blocking = rec.get("blocking_reasons") or []
        if action == "ENTRAR" and confirmed and odd and odd > 1 and edge is not None and edge > 0:
            decision = "ENTRA"
        elif not confirmed:
            reason = "Sem odd real confirmada. Entrada bloqueada para este mercado."
        elif edge is not None and edge <= 0:
            reason = f"Odd confirmada, mas sem valor esperado positivo ({round(edge * 100, 2)}pp)."
        elif blocking:
            reason = "; ".join(str(item) for item in blocking if item)[:280] or reason
        skills.append(
            {
                "name": f"MARKET_INTELLIGENCE_{str(rec.get('market') or 'mercado').upper()}",
                "market": str(rec.get("market") or "Mercado quantitativo"),
                "selection": str(rec.get("selection") or rec.get("entry") or "-"),
                "decision": decision,
                "confidence": confidence,
                "odd": odd,
                "edge": edge,
                "reason": reason or "Mercado quantitativo em monitoramento com filtros de odd real.",
            }
        )
    return skills


def _merge_market_recommendations(signal: dict[str, Any], recommendations: list[dict[str, Any]]) -> None:
    if not recommendations:
        return
    existing = signal.get("market_recommendations")
    merged = list(existing) if isinstance(existing, list) else []
    seen = {
        (
            str(item.get("market") or "").lower(),
            str(item.get("selection") or "").lower(),
            str(item.get("line") or "").lower(),
        )
        for item in merged
        if isinstance(item, dict)
    }
    for rec in recommendations:
        if not isinstance(rec, dict):
            continue
        key = (
            str(rec.get("market") or "").lower(),
            str(rec.get("selection") or "").lower(),
            str(rec.get("line") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        row = dict(rec)
        if not row.get("confirmed") and str(row.get("action") or "").upper() == "ENTRAR":
            row["action"] = "MONITORAR"
        merged.append(row)
    signal["market_recommendations"] = merged[:12]


def _best_skill(skills: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [item for item in skills if isinstance(item, dict) and item.get("name")]
    if not valid:
        return None
    weight = {"ENTRA": 5, "MONITORAR": 4, "SEGURA": 3, "NAO_ENTRAR": 2, "SAI": 1}
    return sorted(
        valid,
        key=lambda item: (
            weight.get(str(item.get("decision") or "").upper(), 0),
            _safe_int(item.get("confidence")),
            _safe_float(item.get("edge"), 0.0),
        ),
        reverse=True,
    )[0]


def _brain_score_note(signal: dict[str, Any]) -> str:
    best = _to_dict((signal.get("brain") or {}).get("best_skill"))
    if not best:
        return str(signal.get("score_note") or "Scanner padrao ativo.")
    decision = str(best.get("decision") or "").upper()
    market = str(best.get("market") or "mercado")
    reason = str(best.get("reason") or "").strip()
    prefix = {
        "ENTRA": "Brain validou entrada",
        "MONITORAR": "Brain sugere monitorar",
        "SEGURA": "Brain sugere segurar",
        "NAO_ENTRAR": "Brain pede cautela",
        "SAI": "Brain pede saida",
    }.get(decision, "Brain ativo")
    summary = f"{prefix} em {market.lower()}."
    if reason:
        return f"{summary} {reason}"
    return summary


def _extract_live_facts(game: dict[str, Any]) -> dict[str, Any]:
    markets = _to_dict(game.get("markets"))
    facts = _to_dict(markets.get("live_facts"))
    corners_live = _to_dict(_to_dict(markets.get("corners")).get("live"))
    cards_live = _to_dict(_to_dict(markets.get("cards")).get("live"))
    if facts.get("corners_home") is None:
        facts["corners_home"] = _safe_int(corners_live.get("home"))
    if facts.get("corners_away") is None:
        facts["corners_away"] = _safe_int(corners_live.get("away"))
    if facts.get("yellow_home") is None:
        facts["yellow_home"] = _safe_int(cards_live.get("yellow_home"))
    if facts.get("yellow_away") is None:
        facts["yellow_away"] = _safe_int(cards_live.get("yellow_away"))
    if facts.get("red_home") is None:
        facts["red_home"] = _safe_int(cards_live.get("red_home"))
    if facts.get("red_away") is None:
        facts["red_away"] = _safe_int(cards_live.get("red_away"))
    facts.setdefault("shots_on_home", _safe_int(game.get("home_shots_on")))
    facts.setdefault("shots_on_away", _safe_int(game.get("away_shots_on")))
    facts.setdefault("possession_home", 50.0)
    facts.setdefault("possession_away", 50.0)
    return facts


def _market_totals(game: dict[str, Any], key: str) -> dict[str, Any]:
    market = _to_dict(_to_dict(game.get("markets")).get(key))
    return {
        "line": _as_text(_to_dict(market.get("over")).get("line")),
        "over_odd": _safe_float(_to_dict(market.get("over")).get("odds"), None),
        "under_odd": _safe_float(_to_dict(market.get("under")).get("odds"), None),
    }


def _pregame_summary(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "league": _as_text(row["league"]),
        "promising_score": _safe_int(row["promising_score"]),
        "focus": _as_text(row["focus"]),
        "starts_in_minutes": _safe_int(row["starts_in_minutes"]),
        "kickoff_at": row["kickoff_at"],
    }


def _season_from_kickoff(value: Any) -> int | None:
    raw = _as_text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).year
    except ValueError:
        return None


def _edge_from_probability(probability: float, odd: float | None) -> float | None:
    if odd is None or odd <= 1:
        return None
    implied = 1 / odd
    return round(probability - implied, 4)


def _age_seconds(value: Any) -> int:
    raw = _as_text(value)
    if not raw:
        return 10_000
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 10_000
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int((datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
