from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import httpx


FOOTBALL_DATA_LEAGUES = [
    ("BRA", "Brasil Serie A"),
    ("BRB", "Brasil Serie B"),
    ("E0", "Premier League"),
    ("SP1", "La Liga"),
    ("D1", "Bundesliga"),
    ("I1", "Serie A Italia"),
    ("F1", "Ligue 1"),
]


def _season_codes(now: datetime) -> list[str]:
    year = now.year % 100
    month = now.month
    if month >= 7:
        return [f"{year:02d}{(year + 1) % 100:02d}", f"{(year - 1) % 100:02d}{year:02d}"]
    return [f"{(year - 1) % 100:02d}{year:02d}", f"{(year - 2) % 100:02d}{(year - 1) % 100:02d}"]


async def scraped_source_memory_rows() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25) as client:
        for league_code, league_name in FOOTBALL_DATA_LEAGUES:
            data = await _fetch_football_data(client, league_code)
            if not data:
                continue
            summary = _summarize_matches(data)
            if not summary:
                continue
            rows.append(
                {
                    "memory_id": f"source_scrape:football_data:{league_code}".lower(),
                    "scope": "source_scrape",
                    "subject": league_name,
                    "source": "football-data.co.uk",
                    "sample_size": summary["matches"],
                    "hit_rate": summary["home_win_rate"],
                    "roi_units": None,
                    "profit_units": 0,
                    "avg_confidence": None,
                    "avg_edge": None,
                    "notes": (
                        f"{league_name}: {summary['matches']} jogos. "
                        f"Gols medio {summary['avg_goals']}. Over2.5 {summary['over25_rate']}%. "
                        f"BTTS {summary['btts_rate']}%. Casa {summary['home_win_rate']}% | "
                        f"Empate {summary['draw_rate']}% | Fora {summary['away_win_rate']}%."
                    ),
                    "payload": {
                        "provider": "football-data.co.uk",
                        "league_code": league_code,
                        "season_candidates": _season_codes(datetime.now(timezone.utc)),
                        "summary": summary,
                    },
                    "updated_at": now.isoformat(),
                }
            )
    return rows


async def _fetch_football_data(client: httpx.AsyncClient, league_code: str) -> list[dict[str, str]]:
    for season in _season_codes(datetime.now(timezone.utc)):
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"
        response = None
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            response = None
        if response is None or response.status_code != 200:
            continue
        text = response.text.strip()
        if not text:
            continue
        reader = csv.DictReader(StringIO(text))
        rows = [row for row in reader if row]
        if rows:
            return rows
    return []


def _summarize_matches(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    matches = 0
    home_wins = 0
    draws = 0
    away_wins = 0
    over25 = 0
    btts = 0
    goals_total = 0

    for row in rows:
        home_goals = _to_int(row.get("FTHG"))
        away_goals = _to_int(row.get("FTAG"))
        result = (row.get("FTR") or "").strip().upper()
        if home_goals is None or away_goals is None:
            continue
        matches += 1
        goals_total += home_goals + away_goals
        if result == "H":
            home_wins += 1
        elif result == "D":
            draws += 1
        elif result == "A":
            away_wins += 1
        if home_goals + away_goals >= 3:
            over25 += 1
        if home_goals > 0 and away_goals > 0:
            btts += 1

    if matches == 0:
        return None

    return {
        "matches": matches,
        "avg_goals": round(goals_total / matches, 2),
        "home_win_rate": round((home_wins / matches) * 100, 2),
        "draw_rate": round((draws / matches) * 100, 2),
        "away_win_rate": round((away_wins / matches) * 100, 2),
        "over25_rate": round((over25 / matches) * 100, 2),
        "btts_rate": round((btts / matches) * 100, 2),
    }


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned.replace(",", ".")))
    except ValueError:
        return None
