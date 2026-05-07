from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.footballQuantAiSkill import get_football_quant_ai_skill


DEFAULT_MARKETS = ["over_2_5", "btts_yes", "match_winner_home"]


def settle_binary_outcome(match: dict[str, Any], market: str) -> int:
    home = int(match.get("home_goals") or 0)
    away = int(match.get("away_goals") or 0)
    total = home + away
    if market == "over_2_5":
        return 1 if total >= 3 else 0
    if market == "under_2_5":
        return 1 if total <= 2 else 0
    if market == "btts_yes":
        return 1 if home > 0 and away > 0 else 0
    if market == "btts_no":
        return 1 if home == 0 or away == 0 else 0
    if market == "match_winner_home":
        return 1 if home > away else 0
    if market == "match_winner_draw":
        return 1 if home == away else 0
    if market == "match_winner_away":
        return 1 if away > home else 0
    return 0


def probability_bucket(probability: float) -> str:
    pct = max(0, min(100, int(round(probability * 100))))
    lower = (pct // 10) * 10
    upper = min(100, lower + 9)
    if lower >= 100:
        return "100"
    return f"{lower:02d}-{upper:02d}"


def run_calibration(
    *,
    markets: list[str],
    league: str | None,
    season: int | None,
    max_matches: int,
    replace: bool,
) -> dict[str, Any]:
    skill = get_football_quant_ai_skill()
    repository = skill.repository
    matches = repository.list_historical_matches(league=league, season=season, limit=max_matches)
    if replace:
        with repository.connect() as conn:
            conn.execute(
                """
                DELETE FROM learning_events
                WHERE event_type = 'historical_calibration'
                AND (? IS NULL OR payload_json LIKE ?)
                AND (? IS NULL OR payload_json LIKE ?)
                """,
                (
                    league,
                    f'%\"league\":\"{league}\"%',
                    season,
                    f'%\"season\":{season}%',
                ),
            )

    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hits": 0, "brier_sum": 0.0, "confidence_sum": 0.0})
    buckets: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"total": 0, "actual": 0}))
    saved = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for match in sorted(matches, key=lambda item: str(item.get("match_date") or "")):
        for market in markets:
            try:
                prediction = skill.prediction.predict_match(int(match["id"]), market=market)
            except Exception as exc:
                skipped += 1
                repository.log(
                    "historical_calibration",
                    "Falha ao calibrar partida historica.",
                    level="warning",
                    payload={"match_id": match.get("id"), "market": market, "error": str(exc)[:300]},
                )
                continue
            actual = settle_binary_outcome(match, market)
            probability = float(prediction.estimated_probability or 0.0)
            brier = round((probability - actual) ** 2, 6)
            hit = (probability >= 0.5 and actual == 1) or (probability < 0.5 and actual == 0)
            payload = {
                "source": "historical_api_football",
                "calibrated_at": now,
                "historical_match_id": int(match["id"]),
                "external_id": match.get("external_id"),
                "league": match.get("league"),
                "season": match.get("season"),
                "match_date": match.get("match_date"),
                "home_team": match.get("home_team"),
                "away_team": match.get("away_team"),
                "score": f"{match.get('home_goals')}x{match.get('away_goals')}",
                "market": market,
                "estimated_probability": probability,
                "confidence_score": float(prediction.confidence_score or 0.0),
                "actual_outcome": actual,
                "calibration_hit": bool(hit),
                "brier_score": brier,
                "recommendation_without_real_odds": prediction.recommendation,
                "note": "Evento de aprendizado estatistico. Sem odd real historica, nao representa entrada/aposta.",
            }
            repository.save_learning_event(
                "historical_calibration",
                payload,
                ref_type="historical_match",
                ref_id=f"{market}:{match['id']}",
            )
            totals[market]["total"] += 1
            totals[market]["hits"] += 1 if hit else 0
            totals[market]["brier_sum"] += brier
            totals[market]["confidence_sum"] += float(prediction.confidence_score or 0.0)
            bucket = probability_bucket(probability)
            buckets[market][bucket]["total"] += 1
            buckets[market][bucket]["actual"] += actual
            saved += 1

    summary: dict[str, Any] = {}
    for market, row in totals.items():
        total = int(row["total"])
        summary[market] = {
            "total": total,
            "directional_hit_rate": round((row["hits"] / total) * 100, 2) if total else 0.0,
            "avg_brier_score": round(row["brier_sum"] / total, 4) if total else None,
            "avg_confidence": round(row["confidence_sum"] / total, 2) if total else 0.0,
            "probability_buckets": {
                bucket: {
                    "total": int(values["total"]),
                    "actual_rate": round((values["actual"] / values["total"]) * 100, 2) if values["total"] else 0.0,
                }
                for bucket, values in sorted(buckets[market].items())
            },
        }

    skill.repository.log(
        "historical_calibration",
        "Calibracao historica concluida.",
        payload={"markets": markets, "league": league, "season": season, "matches": len(matches), "saved_events": saved, "summary": summary},
    )
    return {
        "ok": True,
        "matches_loaded": len(matches),
        "markets": markets,
        "saved_learning_events": saved,
        "skipped": skipped,
        "summary": summary,
        "snapshot": skill.repository.system_snapshot(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibra o motor estatistico com jogos historicos ja importados.")
    parser.add_argument("--markets", default=",".join(DEFAULT_MARKETS), help="Mercados separados por virgula.")
    parser.add_argument("--league", default=None)
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--max-matches", type=int, default=500)
    parser.add_argument("--replace", action="store_true", help="Remove eventos de calibracao anteriores no escopo informado.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markets = [item.strip() for item in str(args.markets or "").split(",") if item.strip()]
    result = run_calibration(
        markets=markets or DEFAULT_MARKETS,
        league=args.league,
        season=args.season,
        max_matches=max(1, args.max_matches),
        replace=bool(args.replace),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
