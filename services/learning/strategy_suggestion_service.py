from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StrategySuggestionService:
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS apex_strategy_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def generate_for_signal(
        self,
        signal: dict[str, Any],
        *,
        drift: dict[str, Any] | None = None,
        rankings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        drift = drift or {}
        rankings = rankings or {}
        suggestions: list[dict[str, Any]] = []
        if float(signal.get("expected_value") or 0.0) < 0:
            suggestions.append(self._persist("Aumentar EV mínimo", "ROI/EV recente pede corte mais rígido antes de liberar entrada.", "high", {"change": "ev_min_up"}))
        if not bool(signal.get("telegram_vip", {}).get("odds_confirmed", signal.get("apex_odds_confirmed", False))):
            suggestions.append(self._persist("Bloquear sinais sem odd real", "Aguardando confirmação de odds reais antes de qualquer liberação VIP.", "high", {"change": "confirmed_odds_only"}))
        league = rankings.get("league") or {}
        if bool(league.get("blocked")):
            suggestions.append(self._persist(f"Evitar liga {league.get('league') or ''}".strip(), "Liga degradada pelo ranking operacional.", "medium", {"league": league}))
        market = rankings.get("market") or {}
        if bool(market.get("blocked")):
            suggestions.append(self._persist(f"Reduzir mercado {market.get('market') or ''}".strip(), "Mercado com ROI/drawdown ruim no backtest automático.", "medium", {"market": market}))
        if float(drift.get("score") or 0.0) >= 0.65:
            suggestions.append(self._persist("Reduzir stake e revisar provider", "Drift alto detectado; segurar agressividade até nova estabilização.", "high", {"drift": drift}))
        if not suggestions:
            suggestions.append(self._persist("Manter modo controlado", "Sem ajuste automático; continuar observando sinais e memória operacional.", "low", {"mode": "hold"}))
        return suggestions

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT suggestion_id, title, reason, severity, status, payload_json, created_at FROM apex_strategy_suggestions ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}")} for row in rows]

    def _persist(self, title: str, reason: str, severity: str, payload: dict[str, Any]) -> dict[str, Any]:
        suggestion_id = uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO apex_strategy_suggestions (
                    suggestion_id, title, reason, severity, status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (suggestion_id, title, reason, severity, json.dumps(payload, ensure_ascii=False), _now_iso()),
            )
        return {
            "suggestion_id": suggestion_id,
            "title": title,
            "reason": reason,
            "severity": severity,
            "status": "pending",
            "payload": payload,
        }


_SERVICES: dict[str, StrategySuggestionService] = {}


def get_strategy_suggestion_service(db_file: str | Path) -> StrategySuggestionService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = StrategySuggestionService(key)
        _SERVICES[key] = service
    return service
