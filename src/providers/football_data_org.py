from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .base import LiveGame, LiveProvider
from src.usage_metrics import UsageTracker


LIVE_STATUSES = {"IN_PLAY", "PAUSED", "EXTRA_TIME", "PENALTY_SHOOTOUT", "LIVE"}


class FootballDataOrgProvider(LiveProvider):
    label = "football-data.org"

    def __init__(
        self,
        api_token: str,
        base_url: str = "https://api.football-data.org/v4",
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
    ):
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.usage_tracker = usage_tracker
        self.cost_per_request_brl = float(cost_per_request_brl or 0)

    async def get_live_games(self) -> list[LiveGame]:
        payload = await self._fetch_matches({"status": "LIVE"})
        return _parse_matches(payload.get("matches") or [], live_only=True)

    async def get_today_games(self) -> list[LiveGame]:
        today = datetime.now(timezone.utc).date().isoformat()
        payload = await self._fetch_matches({"dateFrom": today, "dateTo": today})
        return _parse_matches(payload.get("matches") or [], live_only=False)

    async def _fetch_matches(self, params: dict[str, str]) -> dict:
        headers = {"X-Auth-Token": self.api_token}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.get(f"{self.base_url}/matches", params=params, headers=headers)
                response.raise_for_status()
            except Exception as exc:
                self._track(False, operation="matches", error=exc)
                raise
        self._track(True, operation="matches", response_bytes=len(response.content))
        return response.json()

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
            "football_data_org",
            category="api",
            request_count=1,
            success=success,
            response_bytes=response_bytes,
            estimated_cost_brl=self.cost_per_request_brl,
            operation=operation,
            error=str(error)[:240] if error else None,
        )


def _parse_matches(matches: list[dict], live_only: bool) -> list[LiveGame]:
    games: list[LiveGame] = []
    for match in matches:
        status = str(match.get("status") or "").upper()
        if live_only and status not in LIVE_STATUSES:
            continue
        home = match.get("homeTeam") or {}
        away = match.get("awayTeam") or {}
        if not home or not away:
            continue
        score = match.get("score") or {}
        running = score.get("fullTime") or {}
        minute = int(match.get("minute") or 0)
        home_goals = _safe_int(running.get("home"))
        away_goals = _safe_int(running.get("away"))
        home_pressure, away_pressure = _estimated_pressure(minute, home_goals, away_goals, status)
        games.append(
            LiveGame(
                game_id=f"fdorg-{match.get('id')}",
                league=_league_name(match),
                home=str(home.get("shortName") or home.get("name") or "Home"),
                away=str(away.get("shortName") or away.get("name") or "Away"),
                minute=minute,
                home_goals=home_goals,
                away_goals=away_goals,
                home_pressure=home_pressure,
                away_pressure=away_pressure,
                home_shots_on=max(home_goals, 1 if home_pressure >= 57 else 0),
                away_shots_on=max(away_goals, 1 if away_pressure >= 57 else 0),
                kickoff_at=str(match.get("utcDate") or "").strip() or None,
                status=status,
                state="in" if status in LIVE_STATUSES else status.lower() or None,
                odds_home=None,
                odds_draw=None,
                odds_away=None,
                priority=_priority(_division(_league_name(match), home, away)),
                division=_division(_league_name(match), home, away),
                markets={},
            )
        )
    games.sort(key=lambda game: (game.priority, -game.minute))
    return games


def _league_name(match: dict) -> str:
    competition = match.get("competition") or {}
    area = match.get("area") or {}
    area_name = str(area.get("name") or "").strip()
    competition_name = str(competition.get("name") or competition.get("code") or "football-data.org").strip()
    return f"{area_name} - {competition_name}" if area_name and area_name not in competition_name else competition_name


def _estimated_pressure(minute: int, home_goals: int, away_goals: int, status: str) -> tuple[int, int]:
    home = 50
    away = 50
    if home_goals < away_goals:
        home += 10
        away -= 6
    elif away_goals < home_goals:
        away += 10
        home -= 6
    elif minute >= 60:
        home += 2
        away += 2
    if status == "PAUSED":
        home = max(35, home - 4)
        away = max(35, away - 4)
    time_boost = min(max(minute, 0), 90) // 15
    return min(100, home + time_boost), min(100, away + time_boost)


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _division(league: str, home: dict, away: dict) -> str:
    text = " ".join(
        [
            league,
            str(home.get("name") or ""),
            str(away.get("name") or ""),
        ]
    ).lower()
    if _has_any(text, ("brasileiro serie a", "brasil - campeonato brasileiro serie a", "brazil serie a", "brasileirao serie a")):
        return "Brasil - Serie A"
    if _has_any(text, ("brasileiro serie b", "brasil - campeonato brasileiro serie b", "brazil serie b", "brasileirao serie b")):
        return "Brasil - Serie B"
    if _has_any(text, ("copa do brasil",)):
        return "Brasil - Copa do Brasil"
    if _looks_brazilian_match(text):
        return "Brasil - Times brasileiros"
    return league or "Outras ligas"


def _priority(division: str) -> int:
    if division == "Brasil - Serie A":
        return 0
    if division == "Brasil - Serie B":
        return 1
    if division in {"Brasil - Copa do Brasil", "Brasil - Serie C"}:
        return 2
    if division == "Brasil - Times brasileiros":
        return 3
    return 50


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _looks_brazilian_match(text: str) -> bool:
    return _has_any(
        text,
        (
            "flamengo",
            "palmeiras",
            "corinthians",
            "sao paulo",
            "são paulo",
            "santos",
            "fluminense",
            "vasco",
            "botafogo",
            "gremio",
            "grêmio",
            "internacional",
            "atletico mineiro",
            "atlético mineiro",
            "cruzeiro",
            "bahia",
            "vitoria",
            "vitória",
            "fortaleza",
            "ceara",
            "ceará",
            "sport recife",
            "athletico",
            "coritiba",
            "goias",
            "goiás",
            "bragantino",
            "juventude",
            "chapecoense",
            "criciuma",
            "criciúma",
            "america-mg",
            "américa-mg",
            "avai",
            "avaí",
            "ponte preta",
            "guarani",
            "mirassol",
            "cuiaba",
            "cuiabá",
            "remo",
            "paysandu",
        ),
    )
