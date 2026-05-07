from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from services.footballQuantAiSkill.config import load_research_skill_settings
from services.footballQuantAiSkill.normalization.normalizer import FootballDataNormalizer
from services.footballQuantAiSkill.repository import FootballResearchRepository


CURATED_GLOBAL_LEAGUES: list[tuple[int, str]] = [
    (71, "Brasil Serie A"),
    (72, "Brasil Serie B"),
    (39, "England Premier League"),
    (140, "Spain LaLiga"),
    (135, "Italy Serie A"),
    (78, "Germany Bundesliga"),
    (61, "France Ligue 1"),
    (94, "Portugal Primeira Liga"),
    (88, "Netherlands Eredivisie"),
    (203, "Turkey Super Lig"),
    (2, "UEFA Champions League"),
    (3, "UEFA Europa League"),
    (13, "CONMEBOL Libertadores"),
    (11, "CONMEBOL Sudamericana"),
    (128, "Argentina Liga Profesional"),
    (239, "Colombia Primera A"),
    (265, "Chile Primera Division"),
    (262, "Mexico Liga MX"),
]


class ApiFootballHistoricalIngestor:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        raw_root: Path,
        rate_limit_seconds: float,
        max_requests: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.raw_root = raw_root
        self.rate_limit_seconds = max(0.2, float(rate_limit_seconds))
        self.max_requests = max(1, int(max_requests))
        self.requests = 0
        self.progress_file = raw_root / "progress.json"
        self.progress = self._load_progress()

    def _load_progress(self) -> dict[str, Any]:
        if not self.progress_file.exists():
            return {"done": [], "errors": [], "updated_at": None}
        try:
            return json.loads(self.progress_file.read_text(encoding="utf-8"))
        except Exception:
            return {"done": [], "errors": [], "updated_at": None}

    def _save_progress(self) -> None:
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self.progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.progress_file.write_text(json.dumps(self.progress, ensure_ascii=False, indent=2), encoding="utf-8")

    async def request(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.requests >= self.max_requests:
            raise RuntimeError(f"Limite local de requests atingido ({self.max_requests}).")
        self.requests += 1
        headers = {"x-apisports-key": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}{path}", params=params, headers=headers)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait_seconds = int(retry_after) if str(retry_after or "").isdigit() else 90
            await asyncio.sleep(wait_seconds)
            raise RuntimeError("API-Football retornou 429; ciclo pausado para evitar bloqueio.")
        if response.status_code in {401, 403}:
            raise RuntimeError(f"API-Football retornou {response.status_code}; verifique chave/plano.")
        response.raise_for_status()
        body = response.json()
        errors = body.get("errors")
        if errors:
            raise RuntimeError(f"API-Football retornou erro no corpo: {errors}")
        payload = body.get("response", [])
        if not isinstance(payload, list):
            payload = [payload] if payload else []
        await asyncio.sleep(self.rate_limit_seconds)
        return payload

    async def fixtures(self, league_id: int, season: int) -> list[dict[str, Any]]:
        key = f"fixtures:{league_id}:{season}"
        raw_file = self.raw_root / "fixtures" / str(season) / f"league_{league_id}.json"
        if key in set(self.progress.get("done", [])) and raw_file.exists():
            return json.loads(raw_file.read_text(encoding="utf-8"))
        rows = await self.request("/fixtures", {"league": league_id, "season": season, "status": "FT"})
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.progress.setdefault("done", []).append(key)
        self._save_progress()
        return rows

    async def fixture_statistics(self, fixture_id: int) -> dict[str, Any]:
        rows = await self.request("/fixtures/statistics", {"fixture": fixture_id})
        return normalize_api_football_statistics(rows)

    async def fixture_odds(self, fixture_id: int) -> list[dict[str, Any]]:
        rows = await self.request("/odds", {"fixture": fixture_id})
        return normalize_api_football_odds(rows)


def normalize_api_football_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    parsed: dict[str, Any] = {}
    for index, team_row in enumerate(rows[:2]):
        side = "home" if index == 0 else "away"
        for stat in team_row.get("statistics") or []:
            name = str(stat.get("type") or "").lower()
            value = parse_stat_value(stat.get("value"))
            if "possession" in name:
                parsed[f"possession_{side}"] = value
            elif name == "total shots":
                parsed[f"shots_{side}"] = value
            elif "shots on goal" in name:
                parsed[f"shots_on_{side}"] = value
            elif "corner" in name:
                parsed[f"corners_{side}"] = value
            elif "yellow" in name:
                parsed[f"yellow_{side}"] = value
            elif "red" in name:
                parsed[f"red_{side}"] = value
    return parsed


def normalize_api_football_odds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows[:1]:
        for bookmaker in row.get("bookmakers") or []:
            bookmaker_name = bookmaker.get("name") or "bookmaker"
            for bet in bookmaker.get("bets") or []:
                name = str(bet.get("name") or "")
                values = bet.get("values") or []
                lower = name.lower()
                if any(token in lower for token in ("match winner", "1x2", "winner")):
                    item = {"market": "match_winner", "bookmaker": bookmaker_name, "source": "API-Football"}
                    for value in values:
                        label = str(value.get("value") or "").lower()
                        odd = parse_float(value.get("odd"))
                        if label in {"home", "1"}:
                            item["home_odd"] = odd
                        elif label in {"draw", "x"}:
                            item["draw_odd"] = odd
                        elif label in {"away", "2"}:
                            item["away_odd"] = odd
                    normalized.append(item)
                elif "over/under" in lower or "goals" in lower:
                    for value in values:
                        label = str(value.get("value") or "")
                        odd = parse_float(value.get("odd"))
                        if "2.5" not in label and str(value.get("handicap") or "") != "2.5":
                            continue
                        normalized.append(
                            {
                                "market": "over_under",
                                "line": "2.5",
                                "over_odd": odd if "over" in label.lower() else None,
                                "under_odd": odd if "under" in label.lower() else None,
                                "bookmaker": bookmaker_name,
                                "source": "API-Football",
                            }
                        )
    return normalized


def parse_stat_value(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    text = str(value).replace("%", "").replace(",", ".").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def requested_leagues(max_leagues: int | None) -> list[tuple[int, str]]:
    raw = os.getenv("API_FOOTBALL_HISTORICAL_LEAGUES", "").strip()
    if raw:
        rows: list[tuple[int, str]] = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            league_id, _, label = token.partition(":")
            if league_id.strip().isdigit():
                rows.append((int(league_id.strip()), label.strip() or f"League {league_id.strip()}"))
        if rows:
            return rows[: max_leagues or len(rows)]
    return CURATED_GLOBAL_LEAGUES[: max_leagues or len(CURATED_GLOBAL_LEAGUES)]


def seasons_for_years(years: int) -> list[int]:
    current = datetime.now(timezone.utc).year
    return [current - offset for offset in range(max(1, years))]


def sync_matches_to_supabase(rows: list[dict[str, Any]], *, supabase_url: str | None, service_key: str | None) -> dict[str, Any]:
    if not supabase_url or not service_key:
        return {"enabled": False, "synced": 0, "message": "Supabase não configurado neste ambiente."}
    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/historical_matches?on_conflict=external_id,source"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    payload = [
        {
            "external_id": item["external_id"],
            "league": item["league"],
            "country": item["country"],
            "season": item["season"],
            "match_date": item["match_date"],
            "home_team": item["home_team"],
            "away_team": item["away_team"],
            "status": item["status"],
            "home_goals": item["home_goals"],
            "away_goals": item["away_goals"],
            "source": item["source"],
            "raw_json": json.dumps(item.get("raw_payload") or {}, ensure_ascii=False),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        for item in rows
    ]
    if not payload:
        return {"enabled": True, "synced": 0, "message": "Sem linhas novas para enviar."}
    with httpx.Client(timeout=30) as client:
        response = client.post(endpoint, headers=headers, json=payload)
    if response.status_code >= 400:
        return {
            "enabled": True,
            "synced": 0,
            "status_code": response.status_code,
            "message": response.text[:1000],
        }
    return {"enabled": True, "synced": len(payload), "status_code": response.status_code, "message": "OK"}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    base_settings = load_settings()
    settings = load_research_skill_settings()
    api_key = settings.api_football_key or base_settings.api_football_key
    supabase_url = settings.supabase_url or base_settings.supabase_url
    supabase_service_role_key = settings.supabase_service_role_key or base_settings.supabase_service_role_key
    if not api_key:
        raise SystemExit("API_FOOTBALL_KEY não configurada.")
    repository = FootballResearchRepository(settings.db_file)
    normalizer = FootballDataNormalizer()
    ingestor = ApiFootballHistoricalIngestor(
        api_key=api_key,
        base_url=settings.api_football_base_url,
        raw_root=Path(args.raw_root),
        rate_limit_seconds=args.rate_limit_seconds,
        max_requests=args.max_requests,
    )

    imported_total = 0
    supabase_total = 0
    batches = 0
    errors: list[str] = []
    seasons = seasons_for_years(args.years)
    leagues = requested_leagues(args.max_leagues)
    for season in seasons:
        for league_id, league_name in leagues:
            if ingestor.requests >= args.max_requests:
                break
            try:
                fixtures = await ingestor.fixtures(league_id, season)
            except Exception as exc:
                errors.append(f"{league_id}:{season}:{type(exc).__name__}:{exc}")
                continue
            enriched = []
            for fixture in fixtures[: args.max_fixtures_per_league or len(fixtures)]:
                fixture_id = int((fixture.get("fixture") or {}).get("id") or 0)
                if not fixture_id:
                    continue
                if args.with_stats and ingestor.requests < args.max_requests:
                    try:
                        fixture["statistics"] = await ingestor.fixture_statistics(fixture_id)
                    except Exception as exc:
                        errors.append(f"stats:{fixture_id}:{type(exc).__name__}")
                if args.with_odds and ingestor.requests < args.max_requests:
                    try:
                        fixture["odds"] = await ingestor.fixture_odds(fixture_id)
                    except Exception as exc:
                        errors.append(f"odds:{fixture_id}:{type(exc).__name__}")
                enriched.append(fixture)
            normalized = [normalizer.normalize_match(item, source="API-Football") for item in enriched]
            if not normalized:
                continue
            result = repository.import_normalized_matches(normalized, source_name="API-Football")
            imported_total += int(result.get("imported_matches") or 0)
            batches += 1
            rows_for_supabase = []
            for item in normalized:
                payload = asdict(item)
                payload["match_date"] = item.match_date.isoformat()
                rows_for_supabase.append(payload)
            sync = sync_matches_to_supabase(
                rows_for_supabase,
                supabase_url=supabase_url,
                service_key=supabase_service_role_key,
            )
            supabase_total += int(sync.get("synced") or 0)
            repository.log(
                "apiFootballHistoricalIngestor",
                f"Lote histórico {league_name} {season}",
                payload={
                    "league_id": league_id,
                    "league": league_name,
                    "season": season,
                    "fixtures": len(fixtures),
                    "imported": result,
                    "supabase": sync,
                    "requests_used": ingestor.requests,
                },
            )
        if ingestor.requests >= args.max_requests:
            break
    snapshot = repository.system_snapshot()
    return {
        "ok": True,
        "years": args.years,
        "seasons": seasons,
        "leagues_planned": len(leagues),
        "batches": batches,
        "imported_matches": imported_total,
        "supabase_synced": supabase_total,
        "requests_used": ingestor.requests,
        "errors": errors[:20],
        "snapshot": snapshot,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingestão incremental API-Football -> Research DB/Supabase.")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--max-leagues", type=int, default=6)
    parser.add_argument("--max-requests", type=int, default=40)
    parser.add_argument("--max-fixtures-per-league", type=int, default=0)
    parser.add_argument("--rate-limit-seconds", type=float, default=2.0)
    parser.add_argument("--raw-root", default="data/raw/api_football_historical")
    parser.add_argument("--with-stats", action="store_true")
    parser.add_argument("--with-odds", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    started = time.time()
    result = asyncio.run(run(args))
    result["elapsed_seconds"] = round(time.time() - started, 2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
