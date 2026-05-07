from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from services.footballQuantAiSkill.config import load_research_skill_settings


SYNC_TABLES: dict[str, dict[str, Any]] = {
    "historical_matches": {
        "columns": [
            "external_id",
            "external_fixture_id",
            "source_provider",
            "league_id",
            "league",
            "league_name",
            "country",
            "season",
            "match_date",
            "home_team",
            "away_team",
            "status",
            "home_goals",
            "away_goals",
            "source",
            "raw_json",
            "normalized_payload",
            "data_quality_score",
            "usable_for_training",
            "duplicate_key",
            "temporal_split",
            "import_batch_id",
            "imported_at",
            "created_at",
            "updated_at",
        ],
        "conflict": "external_id,source",
    },
    "historical_odds": {
        "columns": [
            "historical_match_id",
            "timestamp",
            "market",
            "line",
            "home_odd",
            "draw_odd",
            "away_odd",
            "over_odd",
            "under_odd",
            "bookmaker",
            "source",
            "odds_phase",
            "is_real",
            "raw_json",
            "imported_at",
            "created_at",
        ],
        "conflict": None,
    },
    "historical_stats": {
        "columns": [
            "historical_match_id",
            "possession_home",
            "possession_away",
            "shots_home",
            "shots_away",
            "shots_on_home",
            "shots_on_away",
            "corners_home",
            "corners_away",
            "yellow_home",
            "yellow_away",
            "red_home",
            "red_away",
            "dangerous_attacks_home",
            "dangerous_attacks_away",
            "attacks_home",
            "attacks_away",
            "xg_home",
            "xg_away",
            "raw_json",
            "created_at",
            "updated_at",
        ],
        "conflict": "historical_match_id",
    },
    "historical_features": {
        "columns": [
            "match_id",
            "feature_set_version",
            "temporal_split",
            "home_recent_form_5",
            "away_recent_form_5",
            "home_goals_avg_5",
            "away_goals_avg_5",
            "home_conceded_avg_5",
            "away_conceded_avg_5",
            "home_xg_avg_5",
            "away_xg_avg_5",
            "home_strength",
            "away_strength",
            "market_implied_probability",
            "closing_line_value",
            "data_quality_score",
            "usable_for_training",
            "context_match_count",
            "created_at",
        ],
        "conflict": "match_id,feature_set_version",
    },
    "league_reliability_scores": {
        "columns": [
            "league",
            "season",
            "match_count",
            "trainable_count",
            "odds_count",
            "stats_count",
            "avg_data_quality",
            "roi_simulated",
            "drawdown",
            "stability_score",
            "league_reliability_score",
            "classification",
            "reasons_json",
            "calculated_at",
        ],
        "conflict": "league,season",
    },
    "raw_football_imports": {
        "columns": ["source_name", "external_ref", "payload_json", "imported_at"],
        "conflict": "source_name,external_ref",
    },
    "normalized_football_data": {
        "columns": ["entity_type", "entity_key", "normalized_json", "source_name", "created_at"],
        "conflict": "source_name,entity_key",
    },
    "learning_events": {
        "columns": ["event_type", "ref_type", "ref_id", "payload_json", "created_at"],
        "conflict": "event_type,ref_type,ref_id",
    },
    "football_research_logs": {
        "columns": ["level", "component", "message", "payload_json", "created_at"],
        "conflict": None,
    },
}


def rows_from_sqlite(db_file: str, table: str, columns: list[str], limit: int) -> list[dict[str, Any]]:
    query = f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid DESC LIMIT ?"
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, (max(1, limit),)).fetchall()
    return [_normalize_row_for_rest(dict(row)) for row in rows]


def _normalize_row_for_rest(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("usable_for_training", "is_real"):
        if key in row and row[key] is not None:
            row[key] = bool(row[key])
    return row


def rest_upsert(
    *,
    supabase_url: str,
    service_key: str,
    table: str,
    rows: list[dict[str, Any]],
    conflict: str | None,
    batch_size: int,
) -> int:
    if not rows:
        return 0
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal" if conflict else "return=minimal",
    }
    synced = 0
    for index in range(0, len(rows), batch_size):
        batch = rows[index : index + batch_size]
        url = f"{supabase_url.rstrip('/')}/rest/v1/{table}"
        if conflict:
            url = f"{url}?on_conflict={conflict}"
        with httpx.Client(timeout=60) as client:
            response = client.post(url, headers=headers, content=json.dumps(batch, ensure_ascii=False, default=str))
        if response.status_code >= 400:
            if response.status_code == 404 and "Could not find the table" in response.text:
                raise RuntimeError(
                    f"Tabela {table} ainda nao existe no Supabase. "
                    "Aplique migrations/2026_05_06_football_research_history.sql no SQL Editor e rode o sync novamente."
                )
            raise RuntimeError(f"Supabase retornou {response.status_code} em {table}: {response.text[:500]}")
        synced += len(batch)
    return synced


def sync(limit: int, batch_size: int, dry_run: bool) -> dict[str, Any]:
    load_settings()
    settings = load_research_skill_settings()
    supabase_url = settings.supabase_url
    service_key = settings.supabase_service_role_key
    if not supabase_url or not service_key:
        return {
            "ok": False,
            "enabled": False,
            "message": "Supabase nao configurado. Defina SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY no .env.",
            "db_file": settings.db_file,
        }

    summary: dict[str, Any] = {
        "ok": True,
        "enabled": True,
        "dry_run": dry_run,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
    }
    for table, cfg in SYNC_TABLES.items():
        rows = rows_from_sqlite(settings.db_file, table, cfg["columns"], limit)
        if dry_run:
            synced = 0
        else:
            synced = rest_upsert(
                supabase_url=supabase_url,
                service_key=service_key,
                table=table,
                rows=rows,
                conflict=cfg.get("conflict"),
                batch_size=batch_size,
            )
        summary["tables"][table] = {"selected": len(rows), "synced": synced}
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza historico/aprendizado local para Supabase via REST.")
    parser.add_argument("--limit", type=int, default=2000, help="Maximo de linhas por tabela.")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = sync(limit=max(1, args.limit), batch_size=max(1, args.batch_size), dry_run=bool(args.dry_run))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
