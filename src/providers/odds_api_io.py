from __future__ import annotations

import asyncio
from dataclasses import replace
from difflib import SequenceMatcher
import re
import unicodedata

import httpx

from .base import LiveGame, LiveProvider, provider_label
from src.usage_metrics import UsageTracker

_TEAM_STOPWORDS = {
    "ac",
    "afc",
    "association",
    "athletic",
    "atletico",
    "club",
    "clube",
    "cf",
    "de",
    "del",
    "deportivo",
    "do",
    "dos",
    "fc",
    "fk",
    "foot",
    "football",
    "futbol",
    "futebol",
    "la",
    "los",
    "real",
    "s.a.d",
    "sad",
    "sc",
    "sociedad",
    "sport",
    "sporting",
    "sv",
    "team",
    "the",
}

_TOKEN_ALIASES = {
    "athletico": "atletico",
    "al": "",
    "bk": "",
    "cd": "",
    "cs": "",
    "dep": "deportivo",
    "inter": "internacional",
    "man": "manchester",
    "st": "saint",
    "ud": "",
    "uds": "",
    "uni": "united",
    "utd": "united",
}


class OddsApiIoEnricher(LiveProvider):
    def __init__(
        self,
        upstream: LiveProvider,
        api_key: str,
        *,
        base_url: str = "https://api.odds-api.io/v3",
        bookmakers: str = "Bet365",
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
    ):
        self.upstream = upstream
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.bookmakers = [item.strip() for item in str(bookmakers or "Bet365").split(",") if item.strip()]
        self.usage_tracker = usage_tracker
        self.cost_per_request_brl = float(cost_per_request_brl or 0.0)
        self.label = f"{provider_label(upstream)} + Odds-API.io"

    async def get_live_games(self) -> list[LiveGame]:
        games = await self.upstream.get_live_games()
        return await self._enrich_live_games(games)

    async def get_today_games(self) -> list[LiveGame]:
        games = await self.upstream.get_today_games()
        live_games = [game for game in games if int(game.minute or 0) > 0]
        if not live_games:
            return games
        enriched = await self._enrich_live_games(live_games)
        by_id = {game.game_id: game for game in enriched}
        return [by_id.get(game.game_id, game) for game in games]

    async def _enrich_live_games(self, games: list[LiveGame]) -> list[LiveGame]:
        if not games or not self.api_key:
            return games
        async with httpx.AsyncClient(timeout=20) as client:
            events = await self._fetch_live_events(client)
            if not events:
                return games
            matches = self._match_games_to_events(games, events)
            if not matches:
                return games
            tasks = [
                self._fetch_event_odds(client, match["event_id"])
                for match in matches
            ]
            odds_payloads = await asyncio.gather(*tasks, return_exceptions=True)
        enriched: dict[str, LiveGame] = {}
        for match, payload in zip(matches, odds_payloads):
            if isinstance(payload, Exception) or not isinstance(payload, dict):
                continue
            merged = _merge_game_with_odds(match["game"], payload)
            if merged is not None:
                enriched[merged.game_id] = merged
        return [enriched.get(game.game_id, game) for game in games]

    async def _fetch_live_events(self, client: httpx.AsyncClient) -> list[dict]:
        params = {
            "apiKey": self.api_key,
            "sport": "football",
            "status": "live",
            "limit": "120",
        }
        try:
            response = await client.get(f"{self.base_url}/events", params=params)
            response.raise_for_status()
        except Exception as exc:
            self._track(False, operation="events_live", error=exc)
            return []
        self._track(True, operation="events_live", response_bytes=len(response.content))
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("events") or []
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
        return []

    async def _fetch_event_odds(self, client: httpx.AsyncClient, event_id: str) -> dict:
        params = {
            "apiKey": self.api_key,
            "eventId": str(event_id),
        }
        if self.bookmakers:
            params["bookmakers"] = ",".join(self.bookmakers)
        try:
            response = await client.get(f"{self.base_url}/odds", params=params)
            response.raise_for_status()
        except Exception as exc:
            self._track(False, operation="odds_event", error=exc)
            return {}
        self._track(True, operation="odds_event", response_bytes=len(response.content))
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _match_games_to_events(self, games: list[LiveGame], events: list[dict]) -> list[dict[str, object]]:
        used_event_ids: set[str] = set()
        matches: list[dict[str, object]] = []
        for game in games:
            best_event = None
            best_score = 0.0
            for event in events:
                event_id = str(event.get("id") or "")
                if not event_id or event_id in used_event_ids:
                    continue
                score = _event_match_score(game, event)
                if score > best_score:
                    best_score = score
                    best_event = event
            if best_event and best_score >= 0.78:
                event_id = str(best_event.get("id"))
                used_event_ids.add(event_id)
                matches.append(
                    {
                        "game": game,
                        "event": best_event,
                        "event_id": event_id,
                        "score": round(best_score, 4),
                    }
                )
        return matches

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
            "odds_api_io",
            category="api",
            request_count=1,
            success=success,
            response_bytes=response_bytes,
            estimated_cost_brl=self.cost_per_request_brl,
            operation=operation,
            error=str(error)[:240] if error else None,
        )


def _merge_game_with_odds(game: LiveGame, payload: dict) -> LiveGame | None:
    bookmaker_rows = payload.get("bookmakers") or {}
    if not isinstance(bookmaker_rows, dict) or not bookmaker_rows:
        return None
    selected_rows = []
    for name in bookmaker_rows:
        selected_rows.append((str(name), bookmaker_rows.get(name)))
    markets = _parse_bookmaker_markets(selected_rows)
    if not markets:
        return None
    current_markets = dict(game.markets or {})
    current_markets.update(markets)
    one_x_two = markets.get("1x2") or current_markets.get("1x2") or {}
    return replace(
        game,
        odds_home=_coalesce_float(one_x_two.get("home"), game.odds_home),
        odds_draw=_coalesce_float(one_x_two.get("draw"), game.odds_draw),
        odds_away=_coalesce_float(one_x_two.get("away"), game.odds_away),
        markets=current_markets,
    )


def _parse_bookmaker_markets(rows: list[tuple[str, object]]) -> dict[str, dict]:
    parsed: dict[str, dict] = {}
    for _, raw_markets in rows:
        if not isinstance(raw_markets, list):
            continue
        for raw_market in raw_markets:
            if not isinstance(raw_market, dict):
                continue
            name = str(raw_market.get("name") or "").strip().lower()
            entries = raw_market.get("odds") or []
            if "ml" == name or "moneyline" in name or "1x2" in name:
                one_x_two = _parse_ml_market(entries)
                if one_x_two:
                    parsed["1x2"] = one_x_two
            elif "totals" in name or "total" == name:
                totals = _parse_totals_market(entries)
                if totals:
                    parsed["goals"] = totals
    return parsed


def _parse_ml_market(entries: object) -> dict[str, float]:
    if not isinstance(entries, list):
        return {}
    best: dict[str, float] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        home = _safe_float(item.get("home"))
        draw = _safe_float(item.get("draw"))
        away = _safe_float(item.get("away"))
        if home and away:
            best = {}
            if home:
                best["home"] = home
            if draw:
                best["draw"] = draw
            if away:
                best["away"] = away
            break
    return best


def _parse_totals_market(entries: object) -> dict[str, dict[str, float | str | None]]:
    if not isinstance(entries, list):
        return {}
    normalized: list[dict[str, float]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        line = _safe_float(item.get("hdp"))
        over = _safe_float(item.get("over"))
        under = _safe_float(item.get("under"))
        if line is None or (over is None and under is None):
            continue
        normalized.append({"line": line, "over": over, "under": under})
    if not normalized:
        return {}
    normalized.sort(key=lambda item: float(item["line"]))
    picked = normalized[0]
    line_str = _format_line(picked.get("line"))
    return {
        "over": {"line": line_str, "odds": picked.get("over")},
        "under": {"line": line_str, "odds": picked.get("under")},
    }


def _event_match_score(game: LiveGame, event: dict) -> float:
    home_score = _team_match_score(game.home, event.get("home"))
    away_score = _team_match_score(game.away, event.get("away"))
    same_order = (home_score + away_score) / 2
    swapped = (_team_match_score(game.home, event.get("away")) + _team_match_score(game.away, event.get("home"))) / 2
    if swapped >= same_order:
        return 0.0
    league_game = _normalize_text(game.league)
    league_event = _normalize_text((event.get("league") or {}).get("name") if isinstance(event.get("league"), dict) else event.get("league"))
    league_score = 0.0
    if league_game and league_event:
        if league_game == league_event:
            league_score = 1.0
        elif league_game in league_event or league_event in league_game:
            league_score = 0.92
        else:
            league_score = SequenceMatcher(None, league_game, league_event).ratio()
    return round((same_order * 0.84) + (league_score * 0.16), 4)


def _team_match_score(left: object, right: object) -> float:
    left_text = _normalize_text(left)
    right_text = _normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0
    left_tokens = _tokenize_team(left_text)
    right_tokens = _tokenize_team(right_text)
    if not left_tokens or not right_tokens:
        return SequenceMatcher(None, left_text, right_text).ratio()
    if left_tokens == right_tokens:
        return 0.99
    join_left = " ".join(sorted(left_tokens))
    join_right = " ".join(sorted(right_tokens))
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    seq = SequenceMatcher(None, left_text, right_text).ratio()
    seq_tokens = SequenceMatcher(None, join_left, join_right).ratio()
    contained = 1.0 if join_left in join_right or join_right in join_left else 0.0
    return round(max(seq * 0.55 + overlap * 0.35 + contained * 0.1, seq_tokens * 0.85), 4)


def _normalize_text(value: object) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _tokenize_team(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in text.split():
        mapped = _TOKEN_ALIASES.get(token, token)
        if mapped == "":
            continue
        if mapped in _TEAM_STOPWORDS:
            continue
        if len(mapped) <= 1:
            continue
        tokens.add(mapped)
    return tokens


def _safe_float(value: object) -> float | None:
    try:
        return round(float(str(value).replace(",", ".")), 3)
    except (TypeError, ValueError):
        return None


def _format_line(value: object) -> str | None:
    line = _safe_float(value)
    if line is None:
        return None
    if line.is_integer():
        return str(int(line))
    return f"{line:.2f}".rstrip("0").rstrip(".")


def _coalesce_float(primary: object, secondary: object) -> float | None:
    parsed = _safe_float(primary)
    if parsed is not None:
        return parsed
    return _safe_float(secondary)
