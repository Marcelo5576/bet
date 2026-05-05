from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..schemas import NormalizedMatch


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


class FootballDataNormalizer:
    def normalize_match(self, item: dict[str, Any], *, source: str) -> NormalizedMatch:
        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        status = item.get("status") or fixture.get("status") or {}
        event = item.get("event") or {}
        if fixture:
            stats = item.get("statistics") or item.get("stats") or {}
            return NormalizedMatch(
                external_id=str(fixture.get("id") or item.get("id") or ""),
                league=str(league.get("name") or item.get("league") or "Sem liga"),
                country=str(league.get("country") or item.get("country") or ""),
                season=_as_int(league.get("season") or item.get("season"), datetime.now(timezone.utc).year) or datetime.now(timezone.utc).year,
                match_date=_parse_dt(fixture.get("date") or item.get("date")),
                home_team=str((teams.get("home") or {}).get("name") or item.get("home_team") or "Casa"),
                away_team=str((teams.get("away") or {}).get("name") or item.get("away_team") or "Fora"),
                status=str((status.get("short") or status.get("description") or item.get("status") or "scheduled")),
                home_goals=_as_int(goals.get("home")),
                away_goals=_as_int(goals.get("away")),
                minute=_as_int(status.get("elapsed")),
                source=source,
                stats=_normalize_stats(stats),
                odds=_normalize_odds(item.get("odds") or []),
                raw_payload=item,
            )
        if event:
            home = (item.get("homeTeam") or {}).get("name") or item.get("home_team") or "Casa"
            away = (item.get("awayTeam") or {}).get("name") or item.get("away_team") or "Fora"
            return NormalizedMatch(
                external_id=str(item.get("id") or event.get("id") or ""),
                league=str(item.get("competition", {}).get("name") or item.get("league") or "Sem liga"),
                country=str(item.get("competition", {}).get("area", {}).get("name") or item.get("country") or ""),
                season=_as_int(item.get("season", {}).get("startDate", "")[:4], datetime.now(timezone.utc).year) or datetime.now(timezone.utc).year,
                match_date=_parse_dt(item.get("utcDate") or item.get("date")),
                home_team=str(home),
                away_team=str(away),
                status=str(item.get("status") or "scheduled"),
                home_goals=_as_int((item.get("score", {}).get("fullTime") or {}).get("home")),
                away_goals=_as_int((item.get("score", {}).get("fullTime") or {}).get("away")),
                source=source,
                raw_payload=item,
            )
        return NormalizedMatch(
            external_id=str(item.get("external_id") or item.get("id") or item.get("match_id") or ""),
            league=str(item.get("league") or "Sem liga"),
            country=str(item.get("country") or ""),
            season=_as_int(item.get("season"), datetime.now(timezone.utc).year) or datetime.now(timezone.utc).year,
            match_date=_parse_dt(item.get("match_date") or item.get("date")),
            home_team=str(item.get("home_team") or item.get("home") or "Casa"),
            away_team=str(item.get("away_team") or item.get("away") or "Fora"),
            status=str(item.get("status") or "scheduled"),
            home_goals=_as_int(item.get("home_goals")),
            away_goals=_as_int(item.get("away_goals")),
            minute=_as_int(item.get("minute")),
            source=source,
            stats=_normalize_stats(item.get("stats") or item.get("statistics") or {}),
            odds=_normalize_odds(item.get("odds") or []),
            raw_payload=item,
        )


def _normalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    return {
        "possession_home": _as_float(stats.get("possession_home") or stats.get("home_possession")),
        "possession_away": _as_float(stats.get("possession_away") or stats.get("away_possession")),
        "shots_home": _as_int(stats.get("shots_home")),
        "shots_away": _as_int(stats.get("shots_away")),
        "shots_on_home": _as_int(stats.get("shots_on_home")),
        "shots_on_away": _as_int(stats.get("shots_on_away")),
        "corners_home": _as_int(stats.get("corners_home")),
        "corners_away": _as_int(stats.get("corners_away")),
        "yellow_home": _as_int(stats.get("yellow_home")),
        "yellow_away": _as_int(stats.get("yellow_away")),
        "red_home": _as_int(stats.get("red_home")),
        "red_away": _as_int(stats.get("red_away")),
        "dangerous_attacks_home": _as_int(stats.get("dangerous_attacks_home")),
        "dangerous_attacks_away": _as_int(stats.get("dangerous_attacks_away")),
        "attacks_home": _as_int(stats.get("attacks_home")),
        "attacks_away": _as_int(stats.get("attacks_away")),
        "xg_home": _as_float(stats.get("xg_home")),
        "xg_away": _as_float(stats.get("xg_away")),
    }


def _normalize_odds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "timestamp": row.get("timestamp") or row.get("last_update"),
                "market": row.get("market") or row.get("key") or "match_winner",
                "line": row.get("line"),
                "home_odd": _as_float(row.get("home_odd") or row.get("home")),
                "draw_odd": _as_float(row.get("draw_odd") or row.get("draw")),
                "away_odd": _as_float(row.get("away_odd") or row.get("away")),
                "over_odd": _as_float(row.get("over_odd") or row.get("over")),
                "under_odd": _as_float(row.get("under_odd") or row.get("under")),
                "bookmaker": row.get("bookmaker") or row.get("site") or row.get("bookie"),
                "source": row.get("source") or "external",
            }
        )
    return normalized

