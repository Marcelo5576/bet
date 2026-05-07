from __future__ import annotations

import argparse
import asyncio
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
from services.footballQuantAiSkill.repository import FootballResearchRepository


MAIN_BET_IDS = {1: "match_winner", 5: "over_under", 8: "btts"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def average(values: list[float]) -> float | None:
    values = [value for value in values if value and value > 1]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def normalize_main_odds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    timestamp = rows[0].get("update") or (rows[0].get("fixture") or {}).get("date") or _now_iso()
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    bookmakers: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for bookmaker in row.get("bookmakers") or []:
            bookmaker_name = str(bookmaker.get("name") or "bookmaker")
            for bet in bookmaker.get("bets") or []:
                try:
                    bet_id = int(bet.get("id") or 0)
                except Exception:
                    bet_id = 0
                market = MAIN_BET_IDS.get(bet_id)
                if not market:
                    continue
                for value in bet.get("values") or []:
                    label = str(value.get("value") or "").strip().lower()
                    odd = parse_float(value.get("odd"))
                    if not odd:
                        continue
                    bookmakers[market].add(bookmaker_name)
                    if market == "match_winner":
                        if label == "home":
                            buckets[market]["home_odd"].append(odd)
                        elif label == "draw":
                            buckets[market]["draw_odd"].append(odd)
                        elif label == "away":
                            buckets[market]["away_odd"].append(odd)
                    elif market == "over_under":
                        if label == "over 2.5":
                            buckets["over_under:2.5"]["over_odd"].append(odd)
                            bookmakers["over_under:2.5"].add(bookmaker_name)
                        elif label == "under 2.5":
                            buckets["over_under:2.5"]["under_odd"].append(odd)
                            bookmakers["over_under:2.5"].add(bookmaker_name)
                    elif market == "btts":
                        if label == "yes":
                            buckets[market]["home_odd"].append(odd)
                        elif label == "no":
                            buckets[market]["away_odd"].append(odd)

    normalized: list[dict[str, Any]] = []
    for key, values in buckets.items():
        market, _, line = key.partition(":")
        row = {
            "timestamp": timestamp,
            "market": market,
            "line": line or None,
            "home_odd": average(values.get("home_odd", [])),
            "draw_odd": average(values.get("draw_odd", [])),
            "away_odd": average(values.get("away_odd", [])),
            "over_odd": average(values.get("over_odd", [])),
            "under_odd": average(values.get("under_odd", [])),
            "bookmaker": "market_average",
            "source": "API-Football odds consensus",
            "bookmaker_count": len(bookmakers.get(key) or bookmakers.get(market) or []),
        }
        if any(row.get(field) for field in ("home_odd", "draw_odd", "away_odd", "over_odd", "under_odd")):
            normalized.append(row)
    return normalized


class OddsBackfill:
    def __init__(self, *, api_key: str, base_url: str, raw_root: Path, max_requests: int, rate_limit_seconds: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.raw_root = raw_root
        self.max_requests = max(1, int(max_requests))
        self.rate_limit_seconds = max(0.2, float(rate_limit_seconds))
        self.requests = 0
        self.progress_file = raw_root / "progress.json"
        self.progress = self._load_progress()

    def _load_progress(self) -> dict[str, Any]:
        if not self.progress_file.exists():
            return {"done": [], "empty": [], "errors": [], "updated_at": None}
        try:
            return json.loads(self.progress_file.read_text(encoding="utf-8"))
        except Exception:
            return {"done": [], "empty": [], "errors": [], "updated_at": None}

    def _save_progress(self) -> None:
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.progress["updated_at"] = _now_iso()
        self.progress_file.write_text(json.dumps(self.progress, ensure_ascii=False, indent=2), encoding="utf-8")

    async def fetch_fixture_odds(self, fixture_id: str) -> list[dict[str, Any]]:
        raw_file = self.raw_root / "fixtures" / f"{fixture_id}.json"
        if raw_file.exists():
            return json.loads(raw_file.read_text(encoding="utf-8"))
        if self.requests >= self.max_requests:
            raise RuntimeError("Limite local de requests atingido.")
        self.requests += 1
        headers = {"x-apisports-key": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/odds", params={"fixture": fixture_id}, headers=headers)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait_seconds = int(retry_after) if str(retry_after or "").isdigit() else 60
            await asyncio.sleep(wait_seconds)
            raise RuntimeError("API-Football retornou 429; backfill pausado.")
        if response.status_code in {401, 403}:
            raise RuntimeError(f"API-Football retornou {response.status_code}; verifique chave/plano.")
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"API-Football odds erro: {body.get('errors')}")
        rows = body.get("response") or []
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        await asyncio.sleep(self.rate_limit_seconds)
        return rows


def candidate_matches(
    db_file: str,
    *,
    limit: int,
    league: str | None,
    season: int | None,
    include_existing: bool,
    overfetch: int = 5,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if league:
        where.append("m.league = ?")
        params.append(league)
    if season is not None:
        where.append("m.season = ?")
        params.append(season)
    if not include_existing:
        where.append("NOT EXISTS (SELECT 1 FROM historical_odds o WHERE o.historical_match_id = m.id)")
    query = f"""
        SELECT m.id, m.external_id, m.league, m.season, m.match_date, m.home_team, m.away_team
        FROM historical_matches m
        WHERE {' AND '.join(where)}
        ORDER BY m.match_date DESC
        LIMIT ?
    """
    params.append(max(1, int(limit)) * max(1, int(overfetch)))
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def save_odds(repository: FootballResearchRepository, match_id: int, fixture_id: str, raw_rows: list[dict[str, Any]], normalized: list[dict[str, Any]]) -> None:
    now = _now_iso()
    with repository.connect() as conn:
        conn.execute("DELETE FROM historical_odds WHERE historical_match_id = ?", (match_id,))
        conn.execute(
            """
            INSERT INTO raw_football_imports (user_id, source_name, external_ref, payload_json, imported_at)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            ("API-Football odds", fixture_id, json.dumps(raw_rows, ensure_ascii=False), now),
        )
        for odd in normalized:
            conn.execute(
                """
                INSERT INTO historical_odds (
                    user_id, historical_match_id, timestamp, market, line,
                    home_odd, draw_odd, away_odd, over_odd, under_odd, bookmaker, source, created_at
                ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    odd.get("timestamp") or now,
                    odd.get("market"),
                    odd.get("line"),
                    odd.get("home_odd"),
                    odd.get("draw_odd"),
                    odd.get("away_odd"),
                    odd.get("over_odd"),
                    odd.get("under_odd"),
                    odd.get("bookmaker"),
                    odd.get("source"),
                    now,
                ),
            )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    base_settings = load_settings()
    settings = load_research_skill_settings()
    api_key = settings.api_football_key or base_settings.api_football_key
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY não configurada.")
    repository = FootballResearchRepository(settings.db_file)
    backfill = OddsBackfill(
        api_key=api_key,
        base_url=settings.api_football_base_url,
        raw_root=Path(args.raw_root),
        max_requests=args.max_requests,
        rate_limit_seconds=args.rate_limit_seconds,
    )
    matches = candidate_matches(
        settings.db_file,
        limit=args.limit,
        league=args.league,
        season=args.season,
        include_existing=args.include_existing,
        overfetch=args.overfetch,
    )
    known_empty = {str(item) for item in backfill.progress.get("empty", [])}
    known_errors = {
        str(item.get("fixture_id"))
        for item in backfill.progress.get("errors", [])
        if isinstance(item, dict) and item.get("fixture_id")
    }
    skipped_known_empty = 0
    skipped_known_errors = 0
    saved_matches = 0
    saved_rows = 0
    empty = 0
    errors: list[str] = []
    for match in matches:
        if backfill.requests >= backfill.max_requests:
            break
        fixture_id = str(match.get("external_id") or "")
        if not fixture_id:
            continue
        if fixture_id in known_empty and not args.retry_empty:
            skipped_known_empty += 1
            continue
        if fixture_id in known_errors and not args.retry_errors:
            skipped_known_errors += 1
            continue
        try:
            raw = await backfill.fetch_fixture_odds(fixture_id)
            normalized = normalize_main_odds(raw)
            if normalized:
                save_odds(repository, int(match["id"]), fixture_id, raw, normalized)
                saved_matches += 1
                saved_rows += len(normalized)
                backfill.progress.setdefault("done", []).append(fixture_id)
            else:
                empty += 1
                backfill.progress.setdefault("empty", []).append(fixture_id)
            backfill._save_progress()
        except Exception as exc:
            errors.append(f"{fixture_id}:{type(exc).__name__}:{str(exc)[:180]}")
            backfill.progress.setdefault("errors", []).append({"fixture_id": fixture_id, "error": str(exc)[:300], "at": _now_iso()})
            backfill._save_progress()
            if "429" in str(exc):
                break
    repository.log(
        "apiFootballOddsBackfill",
        "Backfill de odds historicas concluido.",
        payload={
            "requested_candidates": len(matches),
            "requests_used": backfill.requests,
            "saved_matches": saved_matches,
            "saved_odds_rows": saved_rows,
            "empty": empty,
            "skipped_known_empty": skipped_known_empty,
            "skipped_known_errors": skipped_known_errors,
            "errors": errors[:20],
        },
    )
    return {
        "ok": True,
        "candidates": len(matches),
        "requests_used": backfill.requests,
        "saved_matches": saved_matches,
        "saved_odds_rows": saved_rows,
        "empty": empty,
        "skipped_known_empty": skipped_known_empty,
        "skipped_known_errors": skipped_known_errors,
        "errors": errors,
        "snapshot": repository.system_snapshot(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Busca odds historicas reais da API-Football para jogos ja importados.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--rate-limit-seconds", type=float, default=1.0)
    parser.add_argument("--league", default=None)
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--retry-empty", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--overfetch", type=int, default=6)
    parser.add_argument("--raw-root", default="data/raw/api_football_odds")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
