from __future__ import annotations

import asyncio
import re

import httpx

from .base import LiveGame, LiveProvider


DEFAULT_SOCCER_LEAGUES = (
    "bra.1",
    "bra.2",
    "bra.3",
    "bra.copa_do_brasil",
    "conmebol.libertadores",
    "conmebol.sudamericana",
    "eng.1",
    "eng.2",
    "eng.fa",
    "eng.league_cup",
    "esp.1",
    "esp.2",
    "esp.copa_del_rey",
    "ita.1",
    "ita.2",
    "ita.coppa_italia",
    "ger.1",
    "ger.2",
    "ger.dfb_pokal",
    "fra.1",
    "fra.2",
    "fra.coupe_de_france",
    "por.1",
    "ned.1",
    "ned.2",
    "tur.1",
    "ksa.1",
    "sau.1",
    "bel.1",
    "sco.1",
    "aut.1",
    "sui.1",
    "den.1",
    "nor.1",
    "swe.1",
    "pol.1",
    "gre.1",
    "jpn.1",
    "kor.1",
    "chn.1",
    "aus.1",
    "zaf.1",
    "egy.1",
    "mar.1",
    "arg.1",
    "arg.2",
    "usa.1",
    "mex.1",
    "mex.2",
    "uru.1",
    "chi.1",
    "col.1",
    "per.1",
    "ecu.1",
    "bol.1",
    "par.1",
    "ven.1",
    "fifa.world",
    "uefa.champions",
    "uefa.europa",
    "uefa.europa.conf",
    "uefa.nations",
    "afc.champions",
    "caf.champions",
)


class EspnProvider(LiveProvider):
    label = "ESPN Scoreboard"

    def __init__(
        self,
        url: str = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard",
        leagues: tuple[str, ...] = DEFAULT_SOCCER_LEAGUES,
    ):
        self.urls = [url] + [
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
            for league in leagues
        ]

    async def get_live_games(self) -> list[LiveGame]:
        return await self._get_games(live_only=True)

    async def get_today_games(self) -> list[LiveGame]:
        return await self._get_games(live_only=False)

    async def _get_games(self, live_only: bool) -> list[LiveGame]:
        async with httpx.AsyncClient(timeout=20) as client:
            payloads = await asyncio.gather(
                *(self._fetch_scoreboard(client, url) for url in self.urls),
                return_exceptions=True,
            )

        games_by_id: dict[str, LiveGame] = {}
        for payload in payloads:
            if isinstance(payload, Exception) or not isinstance(payload, dict):
                continue
            for game in _parse_games(payload, live_only=live_only):
                games_by_id[game.game_id] = game

        games = list(games_by_id.values())
        games.sort(key=lambda game: (game.priority, -game.minute))
        return games

    async def _fetch_scoreboard(self, client: httpx.AsyncClient, url: str) -> dict:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _parse_games(payload: dict, live_only: bool = True) -> list[LiveGame]:
        league_names = _league_names(payload.get("leagues", []))
        games: list[LiveGame] = []
        for event in payload.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            status = competition.get("status", {})
            status_type = status.get("type", {})
            if live_only and status_type.get("state") != "in":
                continue

            teams = _teams_by_side(competition.get("competitors", []))
            if "home" not in teams or "away" not in teams:
                continue

            home = teams["home"]
            away = teams["away"]
            home_stats = _stats(home.get("statistics", []))
            away_stats = _stats(away.get("statistics", []))
            markets = _markets(competition.get("odds", []))
            odds = markets.get("1x2", {})
            league = _league(event, competition, league_names)
            division = _division(league, home, away)
            games.append(
                LiveGame(
                    game_id=f"espn-{event.get('id')}",
                    league=league,
                    home=home.get("team", {}).get("displayName", "Home"),
                    away=away.get("team", {}).get("displayName", "Away"),
                    minute=_minute(status),
                    home_goals=_as_int(home.get("score")),
                    away_goals=_as_int(away.get("score")),
                    home_pressure=_pressure(home_stats),
                    away_pressure=_pressure(away_stats),
                    home_shots_on=_as_int(home_stats.get("shotsOnTarget")),
                    away_shots_on=_as_int(away_stats.get("shotsOnTarget")),
                    odds_home=odds.get("home"),
                    odds_draw=odds.get("draw"),
                    odds_away=odds.get("away"),
                    priority=_priority(division),
                    division=division,
                    markets=markets,
                )
            )
        return games


def _teams_by_side(competitors: list[dict]) -> dict[str, dict]:
    return {
        competitor.get("homeAway", ""): competitor
        for competitor in competitors
        if competitor.get("homeAway")
    }


def _stats(raw: list[dict]) -> dict[str, str]:
    return {item.get("name", ""): item.get("displayValue", "0") for item in raw}


def _pressure(stats: dict[str, str]) -> int:
    possession = _as_float(stats.get("possessionPct"), 50)
    shots = _as_float(stats.get("totalShots"), 0)
    shots_on = _as_float(stats.get("shotsOnTarget"), 0)
    corners = _as_float(stats.get("wonCorners"), 0)
    pressure = possession * 0.55 + min(shots * 4, 25) + min(shots_on * 5, 20) + min(corners * 3, 15)
    return max(0, min(100, int(pressure)))


def _minute(status: dict) -> int:
    display = str(status.get("displayClock") or status.get("type", {}).get("detail") or "")
    match = re.search(r"\d+", display)
    if match:
        return int(match.group(0))
    clock = status.get("clock")
    if isinstance(clock, (int, float)) and clock > 0:
        return int(clock // 60)
    return 45 if status.get("type", {}).get("name") == "STATUS_HALFTIME" else 0


def _league_names(raw: list[dict]) -> dict[str, str]:
    names: dict[str, str] = {}
    for league in raw:
        for key in (league.get("id"), league.get("uid"), league.get("slug")):
            if key:
                names[str(key)] = (
                    league.get("name")
                    or league.get("displayName")
                    or league.get("abbreviation")
                    or str(key)
                )
    return names


def _league(event: dict, competition: dict, league_names: dict[str, str]) -> str:
    league = event.get("league") or competition.get("league") or {}
    if league.get("name") or league.get("displayName"):
        return league.get("name") or league.get("displayName")

    uid = str(event.get("uid") or "")
    for chunk in uid.split("~"):
        if chunk.startswith("l:"):
            league_id = chunk.split(":", 1)[1]
            return league_names.get(league_id, league_names.get(f"s:600~l:{league_id}", "ESPN Soccer"))

    return event.get("season", {}).get("slug", "ESPN Soccer")


def _division(league: str, home: dict, away: dict) -> str:
    text = " ".join(
        [
            league,
            home.get("team", {}).get("displayName", ""),
            away.get("team", {}).get("displayName", ""),
        ]
    ).lower()

    if _has_any(text, ("brasileiro serie a", "brasileirão série a", "brazil serie a", "brasileirao serie a")):
        return "Brasil - Serie A"
    if _has_any(text, ("brasileiro serie b", "brasileirão série b", "brazil serie b", "brasileirao serie b")):
        return "Brasil - Serie B"
    if _has_any(text, ("brasileiro serie c", "brasileirão série c", "brazil serie c", "brasileirao serie c")):
        return "Brasil - Serie C"
    if _has_any(text, ("copa do brasil", "brazil cup")):
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
            "recife",
            "athletico-pr",
            "athletico paranaense",
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


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _markets(raw: list[dict]) -> dict:
    if not raw:
        return {}
    first = next((item for item in raw if isinstance(item, dict)), None)
    if not first:
        return {}
    markets: dict = {}
    moneyline = first.get("moneyline") or {}
    one_x_two = {
        side: _american_to_decimal(
            moneyline.get(side, {}).get("current", {}).get("odds")
        )
        for side in ("home", "draw", "away")
        if moneyline.get(side, {}).get("current", {}).get("odds") not in {None, "OFF"}
    }
    if one_x_two:
        markets["1x2"] = one_x_two

    total = first.get("total") or {}
    goals = {}
    for side in ("over", "under"):
        current = (total.get(side) or {}).get("current") or {}
        if current.get("odds") not in {None, "OFF"}:
            goals[side] = {
                "line": current.get("line"),
                "odds": _american_to_decimal(current.get("odds")),
            }
    if goals:
        markets["goals"] = goals

    spread = first.get("pointSpread") or {}
    asian = {}
    for side in ("home", "away"):
        current = (spread.get(side) or {}).get("current") or {}
        if current.get("odds") not in {None, "OFF"}:
            asian[side] = {
                "line": current.get("line"),
                "odds": _american_to_decimal(current.get("odds")),
            }
    if asian:
        markets["asian"] = asian

    return markets


def _american_to_decimal(value) -> float | None:
    odd = _as_float(value, None)
    if odd is None or odd == 0:
        return None
    if odd > 0:
        return round(1 + odd / 100, 2)
    return round(1 + 100 / abs(odd), 2)


def _as_int(value) -> int:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _as_float(value, default: float | None = 0) -> float | None:
    try:
        return float(str(value).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return default
