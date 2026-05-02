from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def estimate_text_tokens(text: str | None) -> int:
    raw = str(text or "").strip()
    if not raw:
        return 0
    return max(1, round(len(raw) / 4))


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0), 6)
    except (TypeError, ValueError):
        return 0.0


def _merge_operations(raw_json: str | None, operation: str | None, request_count: int) -> str:
    try:
        data = json.loads(raw_json or "{}")
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    if operation:
        data[operation] = int(data.get(operation, 0) or 0) + int(request_count)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _parse_operations(raw_json: str | None) -> dict[str, int]:
    try:
        data = json.loads(raw_json or "{}")
        if not isinstance(data, dict):
            return {}
        result: dict[str, int] = {}
        for key, value in data.items():
            result[str(key)] = _safe_int(value)
        return result
    except Exception:
        return {}


@dataclass(frozen=True)
class UsagePricing:
    gemini_input_cost_per_1m_brl: float = 0.0
    gemini_output_cost_per_1m_brl: float = 0.0
    api_football_cost_per_request_brl: float = 0.0
    football_data_org_cost_per_request_brl: float = 0.0
    odds_api_io_cost_per_request_brl: float = 0.0
    espn_cost_per_request_brl: float = 0.0
    supabase_cost_per_request_brl: float = 0.0
    stripe_cost_per_request_brl: float = 0.0
    mercadopago_cost_per_request_brl: float = 0.0

    def request_cost(self, service: str) -> float:
        mapping = {
            "api_football": self.api_football_cost_per_request_brl,
            "football_data_org": self.football_data_org_cost_per_request_brl,
            "odds_api_io": self.odds_api_io_cost_per_request_brl,
            "espn": self.espn_cost_per_request_brl,
            "supabase": self.supabase_cost_per_request_brl,
            "stripe": self.stripe_cost_per_request_brl,
            "mercadopago": self.mercadopago_cost_per_request_brl,
        }
        return _safe_float(mapping.get(service, 0.0))

    def gemini_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (_safe_int(input_tokens) / 1_000_000) * self.gemini_input_cost_per_1m_brl
        output_cost = (_safe_int(output_tokens) / 1_000_000) * self.gemini_output_cost_per_1m_brl
        return round(input_cost + output_cost, 6)

    def as_dict(self) -> dict[str, float]:
        return {
            "gemini_input_cost_per_1m_brl": self.gemini_input_cost_per_1m_brl,
            "gemini_output_cost_per_1m_brl": self.gemini_output_cost_per_1m_brl,
            "api_football_cost_per_request_brl": self.api_football_cost_per_request_brl,
            "football_data_org_cost_per_request_brl": self.football_data_org_cost_per_request_brl,
            "odds_api_io_cost_per_request_brl": self.odds_api_io_cost_per_request_brl,
            "espn_cost_per_request_brl": self.espn_cost_per_request_brl,
            "supabase_cost_per_request_brl": self.supabase_cost_per_request_brl,
            "stripe_cost_per_request_brl": self.stripe_cost_per_request_brl,
            "mercadopago_cost_per_request_brl": self.mercadopago_cost_per_request_brl,
        }


class UsageTracker:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists service_usage_totals (
                    service text primary key,
                    category text not null default 'api',
                    requests integer not null default 0,
                    success_requests integer not null default 0,
                    error_requests integer not null default 0,
                    input_tokens integer not null default 0,
                    output_tokens integer not null default 0,
                    response_bytes integer not null default 0,
                    estimated_cost_brl real not null default 0,
                    last_request_at text,
                    last_error text,
                    operations_json text not null default '{}'
                )
                """
            )
            conn.execute(
                """
                create table if not exists service_usage_daily (
                    service text not null,
                    day_key text not null,
                    category text not null default 'api',
                    requests integer not null default 0,
                    success_requests integer not null default 0,
                    error_requests integer not null default 0,
                    input_tokens integer not null default 0,
                    output_tokens integer not null default 0,
                    response_bytes integer not null default 0,
                    estimated_cost_brl real not null default 0,
                    last_request_at text,
                    last_error text,
                    operations_json text not null default '{}',
                    primary key (service, day_key)
                )
                """
            )

    def record(
        self,
        service: str,
        *,
        category: str = "api",
        request_count: int = 1,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0,
        response_bytes: int = 0,
        estimated_cost_brl: float = 0.0,
        operation: str | None = None,
        error: str | None = None,
    ) -> None:
        clean_service = str(service or "").strip().lower()
        if not clean_service:
            return
        now = _now_iso()
        day_key = _today_key()
        request_count = max(0, _safe_int(request_count))
        success_count = request_count if success else 0
        error_count = 0 if success else max(1, request_count)
        category = str(category or "api").strip().lower() or "api"
        estimated_cost_brl = round(max(0.0, _safe_float(estimated_cost_brl)), 6)
        response_bytes = max(0, _safe_int(response_bytes))
        input_tokens = max(0, _safe_int(input_tokens))
        output_tokens = max(0, _safe_int(output_tokens))
        last_error = str(error or "").strip()[:300] or None

        with self._connect() as conn:
            for table, key_sql, key_params in (
                ("service_usage_totals", "service = ?", (clean_service,)),
                ("service_usage_daily", "service = ? and day_key = ?", (clean_service, day_key)),
            ):
                row = conn.execute(
                    f"select operations_json from {table} where {key_sql}",
                    key_params,
                ).fetchone()
                operations_json = _merge_operations(
                    row["operations_json"] if row else None,
                    operation,
                    request_count,
                )
                if row:
                    conn.execute(
                        f"""
                        update {table}
                           set category = ?,
                               requests = requests + ?,
                               success_requests = success_requests + ?,
                               error_requests = error_requests + ?,
                               input_tokens = input_tokens + ?,
                               output_tokens = output_tokens + ?,
                               response_bytes = response_bytes + ?,
                               estimated_cost_brl = estimated_cost_brl + ?,
                               last_request_at = ?,
                               last_error = ?,
                               operations_json = ?
                         where {key_sql}
                        """,
                        (
                            category,
                            request_count,
                            success_count,
                            error_count,
                            input_tokens,
                            output_tokens,
                            response_bytes,
                            estimated_cost_brl,
                            now,
                            last_error,
                            operations_json,
                            *key_params,
                        ),
                    )
                else:
                    if table == "service_usage_totals":
                        conn.execute(
                            f"""
                            insert into {table} (
                                service, category, requests, success_requests, error_requests,
                                input_tokens, output_tokens, response_bytes,
                                estimated_cost_brl, last_request_at, last_error, operations_json
                            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                clean_service,
                                category,
                                request_count,
                                success_count,
                                error_count,
                                input_tokens,
                                output_tokens,
                                response_bytes,
                                estimated_cost_brl,
                                now,
                                last_error,
                                operations_json,
                            ),
                        )
                    else:
                        conn.execute(
                            f"""
                            insert into {table} (
                                service, day_key, category, requests, success_requests, error_requests,
                                input_tokens, output_tokens, response_bytes,
                                estimated_cost_brl, last_request_at, last_error, operations_json
                            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                clean_service,
                                day_key,
                                category,
                                request_count,
                                success_count,
                                error_count,
                                input_tokens,
                                output_tokens,
                                response_bytes,
                                estimated_cost_brl,
                                now,
                                last_error,
                                operations_json,
                            ),
                        )

    def summary(self) -> dict[str, Any]:
        today = _today_key()
        with self._connect() as conn:
            totals_rows = conn.execute(
                "select * from service_usage_totals order by estimated_cost_brl desc, requests desc, service asc"
            ).fetchall()
            today_rows = conn.execute(
                "select * from service_usage_daily where day_key = ? order by estimated_cost_brl desc, requests desc, service asc",
                (today,),
            ).fetchall()
        totals = [self._row_to_dict(row) for row in totals_rows]
        daily = [self._row_to_dict(row) for row in today_rows]
        return {
            "generated_at": _now_iso(),
            "today_key": today,
            "totals": self._aggregate(totals),
            "today": self._aggregate(daily),
            "services": totals,
            "services_today": daily,
        }

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "service": str(row["service"]),
            "category": str(row["category"] or "api"),
            "requests": _safe_int(row["requests"]),
            "success_requests": _safe_int(row["success_requests"]),
            "error_requests": _safe_int(row["error_requests"]),
            "input_tokens": _safe_int(row["input_tokens"]),
            "output_tokens": _safe_int(row["output_tokens"]),
            "response_bytes": _safe_int(row["response_bytes"]),
            "estimated_cost_brl": round(_safe_float(row["estimated_cost_brl"]), 4),
            "last_request_at": row["last_request_at"],
            "last_error": row["last_error"],
            "operations": _parse_operations(row["operations_json"]),
        }

    def _aggregate(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        categories: dict[str, int] = {}
        last_request_at = None
        last_error = None
        for row in rows:
            categories[row["category"]] = categories.get(row["category"], 0) + int(row.get("requests", 0) or 0)
            current_last = row.get("last_request_at")
            if current_last and (not last_request_at or str(current_last) > str(last_request_at)):
                last_request_at = current_last
            if not last_error and row.get("last_error"):
                last_error = row.get("last_error")
        return {
            "services": len(rows),
            "requests": sum(int(row.get("requests", 0) or 0) for row in rows),
            "success_requests": sum(int(row.get("success_requests", 0) or 0) for row in rows),
            "error_requests": sum(int(row.get("error_requests", 0) or 0) for row in rows),
            "input_tokens": sum(int(row.get("input_tokens", 0) or 0) for row in rows),
            "output_tokens": sum(int(row.get("output_tokens", 0) or 0) for row in rows),
            "response_bytes": sum(int(row.get("response_bytes", 0) or 0) for row in rows),
            "estimated_cost_brl": round(
                sum(float(row.get("estimated_cost_brl", 0) or 0) for row in rows), 4
            ),
            "api_requests": sum(
                int(row.get("requests", 0) or 0) for row in rows if str(row.get("category")) == "api"
            ),
            "ai_requests": sum(
                int(row.get("requests", 0) or 0) for row in rows if str(row.get("category")) == "ai"
            ),
            "payment_requests": sum(
                int(row.get("requests", 0) or 0) for row in rows if str(row.get("category")) == "payment"
            ),
            "last_request_at": last_request_at,
            "last_error": last_error,
            "categories": categories,
        }
