from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from services.markets.referee_analysis_service import evaluate_cards_market


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


class RefereeIntelligenceService:
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
                CREATE TABLE IF NOT EXISTS referee_profiles (
                    referee_key TEXT PRIMARY KEY,
                    referee_name TEXT,
                    league TEXT NOT NULL,
                    cards_avg REAL NOT NULL DEFAULT 0,
                    cards_ht_avg REAL NOT NULL DEFAULT 0,
                    cards_st_avg REAL NOT NULL DEFAULT 0,
                    fouls_avg REAL NOT NULL DEFAULT 0,
                    red_rate REAL NOT NULL DEFAULT 0,
                    aggression_index REAL NOT NULL DEFAULT 0,
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS referee_profiles_league_idx ON referee_profiles(league, updated_at DESC);
                """
            )

    def record_profile(
        self,
        *,
        referee_name: str | None,
        league: str,
        cards_avg: float,
        cards_ht_avg: float = 0.0,
        cards_st_avg: float = 0.0,
        fouls_avg: float = 0.0,
        red_rate: float = 0.0,
        aggression_index: float = 0.0,
        sample_size: int = 1,
    ) -> None:
        referee_key = self._key(referee_name, league)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO referee_profiles (
                    referee_key, referee_name, league, cards_avg, cards_ht_avg, cards_st_avg,
                    fouls_avg, red_rate, aggression_index, sample_size, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    referee_key,
                    str(referee_name or ""),
                    str(league or ""),
                    float(cards_avg),
                    float(cards_ht_avg),
                    float(cards_st_avg),
                    float(fouls_avg),
                    float(red_rate),
                    float(aggression_index),
                    max(1, int(sample_size)),
                    _now_iso(),
                ),
            )

    def profile_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        referee_name = str(game.get("referee") or "").strip()
        league = str(game.get("league") or game.get("division") or "").strip()
        with self.connect() as conn:
            if referee_name:
                row = conn.execute(
                    "SELECT * FROM referee_profiles WHERE referee_key = ? LIMIT 1",
                    (self._key(referee_name, league),),
                ).fetchone()
                if row:
                    return {**dict(row), "fallback": False}
            if league:
                row = conn.execute(
                    """
                    SELECT league,
                           AVG(cards_avg) AS cards_avg,
                           AVG(cards_ht_avg) AS cards_ht_avg,
                           AVG(cards_st_avg) AS cards_st_avg,
                           AVG(fouls_avg) AS fouls_avg,
                           AVG(red_rate) AS red_rate,
                           AVG(aggression_index) AS aggression_index,
                           SUM(sample_size) AS sample_size
                    FROM referee_profiles
                    WHERE league = ?
                    GROUP BY league
                    LIMIT 1
                    """,
                    (league,),
                ).fetchone()
                if row:
                    payload = dict(row)
                    payload["referee_name"] = referee_name
                    payload["fallback"] = True
                    return payload
        return {
            "referee_name": referee_name,
            "league": league,
            "cards_avg": 0.0,
            "cards_ht_avg": 0.0,
            "cards_st_avg": 0.0,
            "fouls_avg": 0.0,
            "red_rate": 0.0,
            "aggression_index": 0.0,
            "sample_size": 0,
            "fallback": True,
        }

    def evaluate_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        profile = self.profile_for_game(game)
        cards = evaluate_cards_market(game, referee_profile=profile)
        return {
            "status": "fallback_by_league" if profile.get("fallback") else "referee_profile",
            "referee_name": profile.get("referee_name") or game.get("referee"),
            "league": profile.get("league") or game.get("league") or game.get("division"),
            "cards_avg": round(_safe_float(profile.get("cards_avg")), 2),
            "cards_ht_avg": round(_safe_float(profile.get("cards_ht_avg")), 2),
            "cards_st_avg": round(_safe_float(profile.get("cards_st_avg")), 2),
            "red_rate": round(_safe_float(profile.get("red_rate")), 3),
            "aggression_index": round(max(_safe_float(profile.get("aggression_index")), _safe_float(cards.get("aggression_index"))), 1),
            "sample_size": _safe_int(profile.get("sample_size")),
            "cards_market_view": cards,
        }

    def _key(self, referee_name: str | None, league: str | None) -> str:
        return f"{str(league or '').strip().lower()}::{str(referee_name or '').strip().lower()}"


_SERVICES: dict[str, RefereeIntelligenceService] = {}


def get_referee_intelligence_service(db_file: str | Path) -> RefereeIntelligenceService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = RefereeIntelligenceService(key)
        _SERVICES[key] = service
    return service
