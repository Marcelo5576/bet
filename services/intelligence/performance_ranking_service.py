from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PerformanceRankingService:
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

    def refresh_from_memory(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT market, COUNT(*) AS volume,
                       AVG(COALESCE(simulated_profit_loss, 0)) AS avg_profit,
                       AVG(COALESCE(confidence, 0)) AS confidence_avg
                FROM apex_signal_memory
                WHERE TRIM(COALESCE(market, '')) <> ''
                GROUP BY market
                """
            ).fetchall()
            for row in rows:
                roi = round(_safe_float(row["avg_profit"]) * 100.0, 2)
                volume = int(row["volume"] or 0)
                score = self._score(roi=roi, volume=volume, confidence=_safe_float(row["confidence_avg"]))
                classification = self._market_classification(score, volume)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO apex_market_performance (
                        market_key, market, volume, roi, drawdown, data_quality,
                        odds_availability, stability, classification, score, blocked, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["market"]).strip().lower(),
                        str(row["market"]),
                        volume,
                        roi,
                        max(0.0, abs(min(0.0, roi)) / 2.0),
                        min(100.0, max(35.0, _safe_float(row["confidence_avg"]))),
                        100.0,
                        min(100.0, 30.0 + volume * 2.0),
                        classification,
                        score,
                        1 if classification == "ruim" and volume >= 12 else 0,
                        _now_iso(),
                    ),
                )

            league_rows = conn.execute(
                """
                SELECT league, COUNT(*) AS volume,
                       AVG(COALESCE(simulated_profit_loss, 0)) AS avg_profit,
                       AVG(COALESCE(confidence, 0)) AS confidence_avg
                FROM apex_signal_memory
                WHERE TRIM(COALESCE(payload_json, '')) <> '' AND TRIM(COALESCE(league, '')) <> ''
                GROUP BY league
                """
            ).fetchall()
            # Fallback: old rows may not have direct league column; re-hydrate from payload in service consumers.
            for row in league_rows:
                roi = round(_safe_float(row["avg_profit"]) * 100.0, 2)
                volume = int(row["volume"] or 0)
                score = self._score(roi=roi, volume=volume, confidence=_safe_float(row["confidence_avg"]))
                classification = self._league_classification(score, volume)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO apex_league_performance (
                        league_key, league, volume, roi, drawdown, data_quality,
                        odds_availability, stability, classification, score, blocked, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row["league"]).strip().lower(),
                        str(row["league"]),
                        volume,
                        roi,
                        max(0.0, abs(min(0.0, roi)) / 2.0),
                        min(100.0, max(35.0, _safe_float(row["confidence_avg"]))),
                        100.0,
                        min(100.0, 30.0 + volume * 2.0),
                        classification,
                        score,
                        1 if classification == "evitar" and volume >= 12 else 0,
                        _now_iso(),
                    ),
                )
        return {"markets": len(rows), "leagues": len(league_rows)}

    def for_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        market = str(signal.get("entry_market") or signal.get("market_category") or signal.get("market") or "").strip()
        league = str(game.get("league") or game.get("division") or signal.get("league_name") or "").strip()
        league_row = None
        market_row = None
        with self.connect() as conn:
            if market:
                market_row = conn.execute(
                    "SELECT * FROM apex_market_performance WHERE market_key = ? LIMIT 1",
                    (market.lower(),),
                ).fetchone()
            if league:
                league_row = conn.execute(
                    "SELECT * FROM apex_league_performance WHERE league_key = ? LIMIT 1",
                    (league.lower(),),
                ).fetchone()
        return {
            "market": dict(market_row) if market_row else self._fallback_market(signal),
            "league": dict(league_row) if league_row else self._fallback_league(signal),
        }

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self.connect() as conn:
            return {
                "markets": [dict(row) for row in conn.execute("SELECT * FROM apex_market_performance ORDER BY score DESC, volume DESC LIMIT 12").fetchall()],
                "leagues": [dict(row) for row in conn.execute("SELECT * FROM apex_league_performance ORDER BY score DESC, volume DESC LIMIT 12").fetchall()],
            }

    def _score(self, *, roi: float, volume: int, confidence: float) -> float:
        score = 50.0 + (roi * 1.2) + min(18.0, volume * 1.1) + ((confidence - 50.0) * 0.2)
        return max(0.0, min(100.0, round(score, 1)))

    def _market_classification(self, score: float, volume: int) -> str:
        if score >= 72 and volume >= 8:
            return "forte"
        if score <= 42 and volume >= 8:
            return "ruim"
        return "neutro"

    def _league_classification(self, score: float, volume: int) -> str:
        if score >= 72 and volume >= 8:
            return "boa para operar"
        if score <= 42 and volume >= 8:
            return "evitar"
        return "observacao"

    def _fallback_market(self, signal: dict[str, Any]) -> dict[str, Any]:
        fit = _safe_float(signal.get("historical_market_fit_score"), 45.0)
        classification = "forte" if fit >= 70 else "ruim" if fit <= 35 else "neutro"
        return {"market": str(signal.get("entry_market") or signal.get("market") or ""), "score": fit, "classification": classification, "blocked": classification == "ruim"}

    def _fallback_league(self, signal: dict[str, Any]) -> dict[str, Any]:
        history = signal.get("historical_context") if isinstance(signal.get("historical_context"), dict) else {}
        score = _safe_float(signal.get("league_reliability_score") or history.get("league_reliability_score"), 45.0)
        classification = str(history.get("league_classification") or "")
        if not classification:
            classification = "boa para operar" if score >= 70 else "evitar" if score <= 35 else "observacao"
        return {"league": str((signal.get("game") or {}).get("league") or ""), "score": score, "classification": classification, "blocked": classification.startswith("evitar")}


_SERVICES: dict[str, PerformanceRankingService] = {}


def get_performance_ranking_service(db_file: str | Path) -> PerformanceRankingService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = PerformanceRankingService(key)
        _SERVICES[key] = service
    return service
