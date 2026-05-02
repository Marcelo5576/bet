from __future__ import annotations

import httpx

from .base import LiveGame, LiveProvider
from src.usage_metrics import UsageTracker


class ApiFootballProvider(LiveProvider):
    label = "API-Football"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.usage_tracker = usage_tracker
        self.cost_per_request_brl = float(cost_per_request_brl or 0)

    async def get_live_games(self) -> list[LiveGame]:
        headers = {"x-apisports-key": self.api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                fixtures = await client.get(
                    f"{self.base_url}/fixtures",
                    params={"live": "all"},
                    headers=headers,
                )
                fixtures.raise_for_status()
            except Exception as exc:
                self._track(False, operation="fixtures_live", error=exc)
                raise
            self._track(True, operation="fixtures_live", response_bytes=len(fixtures.content))
            payload = fixtures.json().get("response", [])
            odds_by_fixture = await self._get_live_odds(client, headers)

        games: list[LiveGame] = []
        for item in payload:
            fixture = item.get("fixture", {})
            status = fixture.get("status", {})
            teams = item.get("teams", {})
            goals = item.get("goals", {})
            stats = _flatten_stats(item.get("statistics") or [])
            fixture_id = str(fixture.get("id"))
            odds = odds_by_fixture.get(fixture_id, {})
            one_x_two = odds.get("1x2", odds)
            games.append(
                LiveGame(
                    game_id=fixture_id,
                    league=item.get("league", {}).get("name", "Unknown league"),
                    home=teams.get("home", {}).get("name", "Home"),
                    away=teams.get("away", {}).get("name", "Away"),
                    minute=int(status.get("elapsed") or 0),
                    home_goals=int(goals.get("home") or 0),
                    away_goals=int(goals.get("away") or 0),
                    home_pressure=_pressure(stats, "home"),
                    away_pressure=_pressure(stats, "away"),
                    home_shots_on=int(stats.get("home", {}).get("Shots on Goal") or 0),
                    away_shots_on=int(stats.get("away", {}).get("Shots on Goal") or 0),
                    odds_home=one_x_two.get("home"),
                    odds_draw=one_x_two.get("draw"),
                    odds_away=one_x_two.get("away"),
                    markets=odds,
                )
            )
        return games

    async def _get_live_odds(
        self, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> dict[str, dict[str, float]]:
        try:
            response = await client.get(f"{self.base_url}/odds/live", headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._track(False, operation="odds_live", error=exc)
            return {}
        self._track(True, operation="odds_live", response_bytes=len(response.content))
        odds_by_fixture: dict[str, dict[str, float]] = {}
        for item in response.json().get("response", []):
            if item.get("blocked") or item.get("finished"):
                continue
            fixture_id = str(
                item.get("fixture", {}).get("id")
                or item.get("fixture")
                or item.get("id", "")
            )
            if not fixture_id:
                continue
            parsed = _parse_markets(item)
            if parsed:
                odds_by_fixture[fixture_id] = parsed
        return odds_by_fixture

    def _track(
        self,
        success: bool,
        *,
        operation: str,
        response_bytes: int = 0,
        error: Exception | None = None,
    ) -> None:
        if not self.usage_tracker:
            return
        self.usage_tracker.record(
            "api_football",
            category="api",
            request_count=1,
            success=success,
            response_bytes=response_bytes,
            estimated_cost_brl=self.cost_per_request_brl if success or error is not None else 0.0,
            operation=operation,
            error=str(error)[:240] if error else None,
        )


def _parse_markets(item: dict) -> dict:
    parsed: dict = {}
    for bookmaker in item.get("bookmakers", []) or []:
        for bet in bookmaker.get("bets", []) or []:
            bet_name = str(bet.get("name") or bet.get("label") or "").lower()
            values = bet.get("values", []) or []
            if any(token in bet_name for token in ("match winner", "1x2", "winner")):
                parsed["1x2"] = _parse_1x2_values(values)
            elif any(token in bet_name for token in ("over/under", "goals", "total goals")):
                parsed.setdefault("goals", {}).update(_parse_total_values(values))
            elif any(token in bet_name for token in ("asian handicap", "handicap", "spread")):
                parsed.setdefault("asian", {}).update(_parse_handicap_values(values))
            elif "corner" in bet_name:
                parsed.setdefault("corners", {}).update(_parse_total_values(values))
    return {key: value for key, value in parsed.items() if value}


def _parse_1x2_values(values: list[dict]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        label = str(value.get("value") or value.get("label") or "").lower()
        odd = _as_float(value.get("odd") or value.get("odds"))
        if odd is None:
            continue
        if label in {"home", "1"}:
            parsed["home"] = odd
        elif label in {"draw", "x"}:
            parsed["draw"] = odd
        elif label in {"away", "2"}:
            parsed["away"] = odd
    return parsed


def _parse_total_values(values: list[dict]) -> dict:
    parsed: dict = {}
    for value in values:
        label = str(value.get("value") or value.get("label") or "").lower()
        odd = _as_float(value.get("odd") or value.get("odds"))
        if odd is None:
            continue
        side = "over" if "over" in label else "under" if "under" in label else None
        if not side:
            continue
        parsed[side] = {"line": _extract_line(label), "odds": odd}
    return parsed


def _parse_handicap_values(values: list[dict]) -> dict:
    parsed: dict = {}
    for value in values:
        label = str(value.get("value") or value.get("label") or "").lower()
        odd = _as_float(value.get("odd") or value.get("odds"))
        if odd is None:
            continue
        side = "home" if any(token in label for token in ("home", "1")) else "away" if any(token in label for token in ("away", "2")) else None
        if not side:
            continue
        parsed[side] = {"line": _extract_line(label), "odds": odd}
    return parsed


def _extract_line(label: str) -> str | None:
    import re

    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", label)
    return match.group(0).replace(",", ".") if match else None


def _as_float(value) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _flatten_stats(raw: list[dict]) -> dict[str, dict[str, int | str | None]]:
    data = {"home": {}, "away": {}}
    for idx, team_stats in enumerate(raw[:2]):
        side = "home" if idx == 0 else "away"
        for stat in team_stats.get("statistics", []):
            data[side][stat.get("type", "")] = stat.get("value")
    return data


def _pressure(stats: dict[str, dict], side: str) -> int:
    possession = stats.get(side, {}).get("Ball Possession") or "50%"
    attacks = stats.get(side, {}).get("Dangerous Attacks") or 0
    try:
        possession_num = int(str(possession).replace("%", ""))
    except ValueError:
        possession_num = 50
    try:
        attacks_num = int(attacks)
    except (TypeError, ValueError):
        attacks_num = 0
    return min(100, int((possession_num * 0.65) + min(attacks_num, 70) * 0.35))
