from __future__ import annotations

from contextlib import contextmanager
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


class DriftDetectionService:
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
                CREATE TABLE IF NOT EXISTS apex_drift_events (
                    drift_event_id TEXT PRIMARY KEY,
                    drift_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def evaluate_signal(self, signal: dict[str, Any], *, rankings: dict[str, Any] | None = None) -> dict[str, Any]:
        rankings = rankings or {}
        market = rankings.get("market") or {}
        league = rankings.get("league") or {}
        roi_values = [_safe_float(market.get("roi")), _safe_float(league.get("roi"))]
        worst_roi = min(roi_values) if roi_values else 0.0
        drawdown = max(_safe_float(market.get("drawdown")), _safe_float(league.get("drawdown")))
        odds_shift = 100.0 - min(100.0, _safe_float(signal.get("data_quality"), 0.0))
        score = max(0.0, min(1.0, ((abs(min(0.0, worst_roi)) / 20.0) * 0.5) + (drawdown / 100.0 * 0.3) + (odds_shift / 100.0 * 0.2)))
        severity = "high" if score >= 0.65 else "medium" if score >= 0.35 else "low"
        drift_type = "performance_drift" if worst_roi < 0 else "stable"
        message = (
            "ROI/drawdown indicam mercado degradando; revisar filtros."
            if drift_type == "performance_drift"
            else "Sem drift crítico neste recorte."
        )
        event = {
            "drift_type": drift_type,
            "scope": str(signal.get("entry_market") or signal.get("market") or "global"),
            "severity": severity,
            "score": round(score, 4),
            "message": message,
            "roi": round(worst_roi, 2),
            "drawdown": round(drawdown, 2),
            "data_quality_gap": round(odds_shift, 2),
        }
        self.record_event(event)
        return event

    def record_event(self, payload: dict[str, Any]) -> str:
        drift_event_id = uuid4().hex
        import json

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO apex_drift_events (
                    drift_event_id, drift_type, scope, severity, score, message, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    drift_event_id,
                    str(payload.get("drift_type") or "stable"),
                    str(payload.get("scope") or "global"),
                    str(payload.get("severity") or "low"),
                    _safe_float(payload.get("score")),
                    str(payload.get("message") or ""),
                    json.dumps(payload, ensure_ascii=False),
                    _now_iso(),
                ),
            )
        return drift_event_id

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT drift_type, scope, severity, score, message, payload_json, created_at FROM apex_drift_events ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        import json

        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}")} for row in rows]


_SERVICES: dict[str, DriftDetectionService] = {}


def get_drift_detection_service(db_file: str | Path) -> DriftDetectionService:
    key = str(Path(db_file).expanduser().resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = DriftDetectionService(key)
        _SERVICES[key] = service
    return service
