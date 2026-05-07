from __future__ import annotations

import argparse
from collections import defaultdict
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


def fetch_rows(db_file: str, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def compact_match(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "external_id": row.get("external_id"),
        "league": row.get("league"),
        "country": row.get("country"),
        "season": row.get("season"),
        "date": row.get("match_date"),
        "home": row.get("home_team"),
        "away": row.get("away_team"),
        "score": f"{row.get('home_goals')}x{row.get('away_goals')}",
        "source": row.get("source"),
    }


def compact_learning(row: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except Exception:
        payload = {}
    return {
        "id": row.get("id"),
        "event_type": row.get("event_type"),
        "ref_id": row.get("ref_id"),
        "league": payload.get("league"),
        "season": payload.get("season"),
        "market": payload.get("market"),
        "estimated_probability": payload.get("estimated_probability"),
        "confidence_score": payload.get("confidence_score"),
        "actual_outcome": payload.get("actual_outcome"),
        "calibration_hit": payload.get("calibration_hit"),
        "brier_score": payload.get("brier_score"),
        "expected_value": payload.get("expected_value"),
        "offered_odd": payload.get("offered_odd"),
        "entry_allowed": payload.get("entry_allowed"),
        "result": payload.get("result"),
        "profit_paper": payload.get("profit_paper"),
    }


def summarize_odds_ev(db_file: str, limit: int) -> dict[str, dict[str, Any]]:
    rows = fetch_rows(
        db_file,
        """
        SELECT payload_json
        FROM learning_events
        WHERE event_type = 'historical_odds_ev'
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    )
    summary: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"analyzed": 0, "entries": 0, "wins": 0, "losses": 0, "profit": 0.0, "staked": 0.0, "ev_sum": 0.0, "confidence_sum": 0.0}
    )
    for row in rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            continue
        market = str(payload.get("market") or "unknown")
        odd = float(payload.get("offered_odd") or 0)
        stake = float(payload.get("stake_paper") or 0)
        profit = float(payload.get("profit_paper") or 0)
        entry_allowed = bool(payload.get("entry_allowed"))
        result = str(payload.get("result") or "")
        summary[market]["analyzed"] += 1
        summary[market]["ev_sum"] += float(payload.get("expected_value") or 0)
        summary[market]["confidence_sum"] += float(payload.get("confidence_score") or 0)
        if entry_allowed and stake > 0:
            summary[market]["entries"] += 1
            summary[market]["staked"] += stake
            summary[market]["profit"] += profit
            summary[market]["wins"] += 1 if result == "WIN" else 0
            summary[market]["losses"] += 1 if result == "LOSS" else 0
    output: dict[str, dict[str, Any]] = {}
    for market, item in summary.items():
        analyzed = int(item["analyzed"])
        entries = int(item["entries"])
        staked = float(item["staked"])
        output[market] = {
            "analyzed": analyzed,
            "entries": entries,
            "wins": int(item["wins"]),
            "losses": int(item["losses"]),
            "hit_rate": round((item["wins"] / entries) * 100, 2) if entries else 0,
            "profit_paper": round(item["profit"], 2),
            "roi_on_staked": round((item["profit"] / staked) * 100, 2) if staked else 0,
            "avg_ev": round(item["ev_sum"] / analyzed, 4) if analyzed else 0,
            "avg_confidence": round(item["confidence_sum"] / analyzed, 2) if analyzed else 0,
        }
    return output


def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def build_memory_records(db_file: str, *, max_matches: int, max_learning: int, chunk_size: int) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat()
    counts = {
        table: fetch_rows(db_file, f"SELECT COUNT(*) AS total FROM {table}")[0]["total"]
        for table in [
            "historical_matches",
            "historical_stats",
            "historical_odds",
            "historical_features",
            "league_reliability_scores",
            "learning_events",
            "simulation_runs",
            "simulation_results",
        ]
    }
    quality_summary = fetch_rows(
        db_file,
        """
        SELECT COUNT(*) AS total_matches,
               SUM(CASE WHEN usable_for_training = 1 THEN 1 ELSE 0 END) AS trainable_matches,
               ROUND(AVG(data_quality_score), 2) AS avg_data_quality,
               SUM(CASE WHEN data_quality_score < 70 THEN 1 ELSE 0 END) AS consultation_only
        FROM historical_matches
        """,
    )[0]
    reliable_leagues = fetch_rows(
        db_file,
        """
        SELECT league, season, match_count, trainable_count, odds_count, stats_count,
               avg_data_quality, league_reliability_score, classification
        FROM league_reliability_scores
        ORDER BY league_reliability_score DESC, match_count DESC
        LIMIT 12
        """,
    )
    league_rows = fetch_rows(
        db_file,
        """
        SELECT league, season, COUNT(*) AS total
        FROM historical_matches
        GROUP BY league, season
        ORDER BY total DESC, league ASC
        LIMIT 50
        """,
    )
    calibration_rows = fetch_rows(
        db_file,
        """
        SELECT payload_json
        FROM learning_events
        WHERE event_type = 'historical_calibration'
        ORDER BY id DESC
        LIMIT ?
        """,
        (max_learning,),
    )
    calibration: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0, "brier_sum": 0.0, "confidence_sum": 0.0})
    league_calibration: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0, "brier_sum": 0.0, "confidence_sum": 0.0})
    market_league_calibration: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "hits": 0, "brier_sum": 0.0, "confidence_sum": 0.0}
    )
    for row in calibration_rows:
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            continue
        market = str(payload.get("market") or "unknown")
        league = str(payload.get("league") or "unknown")
        hit_value = 1 if payload.get("calibration_hit") else 0
        brier_value = float(payload.get("brier_score") or 0)
        confidence_value = float(payload.get("confidence_score") or 0)
        calibration[market]["total"] += 1
        calibration[market]["hits"] += hit_value
        calibration[market]["brier_sum"] += brier_value
        calibration[market]["confidence_sum"] += confidence_value
        league_calibration[league]["total"] += 1
        league_calibration[league]["hits"] += hit_value
        league_calibration[league]["brier_sum"] += brier_value
        league_calibration[league]["confidence_sum"] += confidence_value
        market_league_calibration[(league, market)]["total"] += 1
        market_league_calibration[(league, market)]["hits"] += hit_value
        market_league_calibration[(league, market)]["brier_sum"] += brier_value
        market_league_calibration[(league, market)]["confidence_sum"] += confidence_value

    calibration_summary = {}
    for market, item in calibration.items():
        total = int(item["total"])
        calibration_summary[market] = {
            "total": total,
            "directional_hit_rate": round((item["hits"] / total) * 100, 2) if total else 0,
            "avg_brier_score": round(item["brier_sum"] / total, 4) if total else None,
            "avg_confidence": round(item["confidence_sum"] / total, 2) if total else 0,
        }

    records: list[dict[str, Any]] = [
        {
            "memory_id": "football_research:historical_summary",
            "scope": "football_research_history",
            "subject": "API-Football histórico mundial",
            "source": "football_quant_ai_skill",
            "sample_size": int(counts["historical_matches"]),
            "hit_rate": None,
            "roi_units": 0,
            "profit_units": 0,
            "avg_confidence": None,
            "avg_edge": 0,
            "notes": "Resumo do histórico real importado para treinamento paper. Não executa apostas reais.",
            "payload": {
                "created_at": now,
                "counts": counts,
                "by_league_season": league_rows,
                "warning": "Histórico estatístico; entradas reais continuam bloqueadas sem odds confirmadas.",
            },
        },
        {
            "memory_id": "football_research:calibration_summary",
            "scope": "football_research_learning",
            "subject": "Calibração histórica da IA",
            "source": "football_quant_ai_skill",
            "sample_size": sum(item["total"] for item in calibration_summary.values()),
            "hit_rate": round(sum(item["directional_hit_rate"] for item in calibration_summary.values()) / max(1, len(calibration_summary)), 2)
            if calibration_summary
            else 0,
            "roi_units": 0,
            "profit_units": 0,
            "avg_confidence": round(sum(item["avg_confidence"] for item in calibration_summary.values()) / max(1, len(calibration_summary)), 2)
            if calibration_summary
            else 0,
            "avg_edge": 0,
            "notes": "Memória objetiva de acerto/erro por mercado, usada para reduzir opinião genérica da IA.",
            "payload": {"created_at": now, "markets": calibration_summary},
        },
        {
            "memory_id": "football_research:historical_quality_summary",
            "scope": "football_research_quality",
            "subject": "Qualidade da base histórica e split temporal",
            "source": "football_quant_ai_skill",
            "sample_size": int(quality_summary.get("total_matches") or 0),
            "hit_rate": None,
            "roi_units": 0,
            "profit_units": 0,
            "avg_confidence": None,
            "avg_edge": 0,
            "notes": "Score de qualidade: +25 placar, +25 odds reais, +20 stats, +15 normalização, +15 sem duplicata. Abaixo de 70 fica só consulta.",
            "payload": {
                "created_at": now,
                "quality_summary": quality_summary,
                "counts": counts,
                "league_reliability": reliable_leagues,
                "no_leakage_rule": "Backtests usam get_training_context(match_date), sempre com dados anteriores ao jogo alvo.",
            },
        },
    ]

    odds_ev_summary = summarize_odds_ev(db_file, max_learning)
    if odds_ev_summary:
        total_analyzed = sum(item["analyzed"] for item in odds_ev_summary.values())
        total_entries = sum(item["entries"] for item in odds_ev_summary.values())
        total_wins = sum(item["wins"] for item in odds_ev_summary.values())
        total_profit = round(sum(item["profit_paper"] for item in odds_ev_summary.values()), 2)
        records.append(
            {
                "memory_id": "football_research:odds_ev_summary",
                "scope": "football_research_odds_ev",
                "subject": "EV com odds históricas reais",
                "source": "football_quant_ai_skill",
                "sample_size": total_analyzed,
                "hit_rate": round((total_wins / total_entries) * 100, 2) if total_entries else 0,
                "roi_units": None,
                "profit_units": total_profit,
                "avg_confidence": round(
                    sum(item["avg_confidence"] for item in odds_ev_summary.values()) / max(1, len(odds_ev_summary)),
                    2,
                ),
                "avg_edge": round(sum(item["avg_ev"] for item in odds_ev_summary.values()) / max(1, len(odds_ev_summary)), 4),
                "notes": "Resumo paper com odds históricas reais. Ajuda a IA a separar padrão estatístico de preço/EV.",
                "payload": {"created_at": now, "markets": odds_ev_summary},
            }
        )
        for market, item in sorted(odds_ev_summary.items()):
            records.append(
                {
                    "memory_id": f"football_research:odds_ev:{market}".lower()[:500],
                    "scope": "football_research_odds_ev",
                    "subject": market[:240],
                    "source": "football_quant_ai_skill",
                    "sample_size": item["analyzed"],
                    "hit_rate": item["hit_rate"],
                    "roi_units": item["roi_on_staked"],
                    "profit_units": item["profit_paper"],
                    "avg_confidence": item["avg_confidence"],
                    "avg_edge": item["avg_ev"],
                    "notes": "Performance paper do mercado usando odds históricas reais e filtros EV/confiança.",
                    "payload": {"created_at": now, "market": market, **item},
                }
            )

    for league, item in sorted(league_calibration.items()):
        total = int(item["total"])
        records.append(
            {
                "memory_id": f"football_research:league:{league}".lower()[:500],
                "scope": "football_research_league",
                "subject": league[:240],
                "source": "football_quant_ai_skill",
                "sample_size": total,
                "hit_rate": round((item["hits"] / total) * 100, 2) if total else 0,
                "roi_units": 0,
                "profit_units": 0,
                "avg_confidence": round(item["confidence_sum"] / total, 2) if total else 0,
                "avg_edge": 0,
                "notes": "Resumo histórico por liga para a IA usar como contexto de scanner/backtest.",
                "payload": {
                    "created_at": now,
                    "league": league,
                    "avg_brier_score": round(item["brier_sum"] / total, 4) if total else None,
                    "markets": {
                        market: {
                            "total": int(values["total"]),
                            "hit_rate": round((values["hits"] / values["total"]) * 100, 2) if values["total"] else 0,
                            "avg_confidence": round(values["confidence_sum"] / values["total"], 2) if values["total"] else 0,
                            "avg_brier_score": round(values["brier_sum"] / values["total"], 4) if values["total"] else None,
                        }
                        for (item_league, market), values in sorted(market_league_calibration.items())
                        if item_league == league
                    },
                },
            }
        )

    for market, item in sorted(calibration.items()):
        total = int(item["total"])
        records.append(
            {
                "memory_id": f"football_research:market:{market}".lower()[:500],
                "scope": "football_research_market",
                "subject": market[:240],
                "source": "football_quant_ai_skill",
                "sample_size": total,
                "hit_rate": round((item["hits"] / total) * 100, 2) if total else 0,
                "roi_units": 0,
                "profit_units": 0,
                "avg_confidence": round(item["confidence_sum"] / total, 2) if total else 0,
                "avg_edge": 0,
                "notes": "Resumo histórico por mercado para calibrar confiança e reduzir sinais ruins.",
                "payload": {
                    "created_at": now,
                    "market": market,
                    "avg_brier_score": round(item["brier_sum"] / total, 4) if total else None,
                },
            }
        )

    matches = fetch_rows(
        db_file,
        """
        SELECT *
        FROM historical_matches
        ORDER BY match_date DESC
        LIMIT ?
        """,
        (max_matches,),
    )
    for index, batch in enumerate(chunks([compact_match(row) for row in matches], chunk_size), start=1):
        records.append(
            {
                "memory_id": f"football_research:matches:{index:04d}",
                "scope": "football_research_history",
                "subject": f"Jogos históricos lote {index:04d}",
                "source": "football_quant_ai_skill",
                "sample_size": len(batch),
                "hit_rate": None,
                "roi_units": 0,
                "profit_units": 0,
                "avg_confidence": None,
                "avg_edge": 0,
                "notes": "Lote compacto de partidas históricas reais para consulta da IA.",
                "payload": {"created_at": now, "rows": batch},
            }
        )

    learning = fetch_rows(
        db_file,
        """
        SELECT *
        FROM learning_events
        WHERE event_type = 'historical_calibration'
        ORDER BY id DESC
        LIMIT ?
        """,
        (max_learning,),
    )
    for index, batch in enumerate(chunks([compact_learning(row) for row in learning], chunk_size), start=1):
        records.append(
            {
                "memory_id": f"football_research:learning:{index:04d}",
                "scope": "football_research_learning",
                "subject": f"Eventos de calibração lote {index:04d}",
                "source": "football_quant_ai_skill",
                "sample_size": len(batch),
                "hit_rate": round((sum(1 for row in batch if row.get("calibration_hit")) / max(1, len(batch))) * 100, 2),
                "roi_units": 0,
                "profit_units": 0,
                "avg_confidence": round(sum(float(row.get("confidence_score") or 0) for row in batch) / max(1, len(batch)), 2),
                "avg_edge": 0,
                "notes": "Eventos históricos de calibração. Sem odds reais, não representam entrada.",
                "payload": {"created_at": now, "rows": batch},
            }
        )

    return records


def upsert_memory(supabase_url: str, service_key: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    synced = 0
    for batch in chunks(rows, batch_size):
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{supabase_url.rstrip('/')}/rest/v1/betsignal_ai_memory?on_conflict=memory_id",
                headers=headers,
                content=json.dumps(batch, ensure_ascii=False, default=str),
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase retornou {response.status_code} em betsignal_ai_memory: {response.text[:500]}")
        synced += len(batch)
    return synced


def sync(*, max_matches: int, max_learning: int, chunk_size: int, batch_size: int, dry_run: bool) -> dict[str, Any]:
    load_settings()
    settings = load_research_skill_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return {"ok": False, "message": "SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY nao configurados."}
    records = build_memory_records(
        settings.db_file,
        max_matches=max(1, max_matches),
        max_learning=max(1, max_learning),
        chunk_size=max(5, chunk_size),
    )
    synced = 0 if dry_run else upsert_memory(settings.supabase_url, settings.supabase_service_role_key, records, max(1, batch_size))
    return {
        "ok": True,
        "dry_run": dry_run,
        "records_prepared": len(records),
        "records_synced": synced,
        "target_table": "betsignal_ai_memory",
        "note": "Fallback seguro enquanto tabelas historicas dedicadas nao aparecem no schema REST.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza pesquisa historica como memoria da IA no Supabase.")
    parser.add_argument("--max-matches", type=int, default=470)
    parser.add_argument("--max-learning", type=int, default=660)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = sync(
        max_matches=args.max_matches,
        max_learning=args.max_learning,
        chunk_size=args.chunk_size,
        batch_size=args.batch_size,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
