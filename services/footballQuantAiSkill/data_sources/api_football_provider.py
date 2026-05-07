from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import logging
import threading
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from src.cache import TTLCache, get_runtime_cache
from src.rate_limiter import (
    ProviderRateLimiter,
    get_provider_limiter,
    retry_after_seconds,
    sanitize_text,
)
from src.usage_metrics import UsageTracker


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_int(value: Any, default: Any = 0) -> Any:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    raw = str(value).replace("%", "").replace(",", ".").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _body_errors(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    errors_raw = body.get("errors", [])
    if isinstance(errors_raw, dict):
        return [f"{key}: {value}" for key, value in errors_raw.items() if value]
    if isinstance(errors_raw, list):
        return [str(item) for item in errors_raw if item]
    if errors_raw:
        return [str(errors_raw)]
    return []


def _extract_line(label: str) -> str | None:
    import re

    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(label or ""))
    return match.group(0).replace(",", ".") if match else None


def _text_key(value: Any) -> str:
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text.lower().strip())


def normalize_market_name(api_market_name: Any) -> str:
    name = _text_key(api_market_name)
    if not name:
        return "UNSUPPORTED"
    if any(token in name for token in ("match winner", "1x2", "home/draw/away", "fulltime result", "resultado final")):
        return "1X2"
    if name in {"winner", "vencedor"}:
        return "1X2"
    if any(token in name for token in ("both teams score", "both teams to score", "btts", "ambas marcam")):
        return "BTTS"
    if any(token in name for token in ("over/under", "goals over", "total goals", "match goals", "partida - gols")):
        return "OVER_UNDER"
    if "goal" in name and ("over" in name or "under" in name):
        return "OVER_UNDER"
    if any(token in name for token in ("asian handicap", "handicap", "spread")):
        return "ASIAN_HANDICAP"
    if "corner" in name or "escanteio" in name:
        return "CORNERS"
    if "card" in name or "cart" in name:
        return "CARDS"
    return "UNSUPPORTED"


def normalize_selection_name(api_selection: Any, home_team: Any = "", away_team: Any = "") -> str:
    raw = str(api_selection or "").strip()
    label = _text_key(raw)
    home = _text_key(home_team)
    away = _text_key(away_team)
    if not label:
        return "unsupported"
    if label in {"home", "1", "casa"} or (home and label == home):
        return "home"
    if label in {"draw", "x", "empate"}:
        return "draw"
    if label in {"away", "2", "fora"} or (away and label == away):
        return "away"
    if home and (label.startswith(home + " ") or home in label):
        return "home"
    if away and (label.startswith(away + " ") or away in label):
        return "away"
    if "over" in label:
        line = _extract_line(raw)
        return f"over_{line.replace('.', '_')}" if line else "over"
    if "under" in label:
        line = _extract_line(raw)
        return f"under_{line.replace('.', '_')}" if line else "under"
    if label in {"yes", "sim"}:
        return "btts_yes"
    if label in {"no", "nao", "não"}:
        return "btts_no"
    return "unsupported"


def _parse_1x2_values(values: list[dict[str, Any]], home_team: Any = "", away_team: Any = "") -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        label = normalize_selection_name(value.get("value") or value.get("label"), home_team, away_team)
        odd = _safe_float(value.get("odd") or value.get("odds"), None)
        if odd is None:
            continue
        if label == "home":
            parsed["home"] = odd
        elif label == "draw":
            parsed["draw"] = odd
        elif label == "away":
            parsed["away"] = odd
    return parsed


def _parse_total_values(values: list[dict[str, Any]]) -> dict[str, dict[str, float | str | None]]:
    parsed: dict[str, dict[str, float | str | None]] = {}
    for value in values:
        label = str(value.get("value") or value.get("label") or "").lower()
        odd = _safe_float(value.get("odd") or value.get("odds"), None)
        if odd is None:
            continue
        side = "over" if "over" in label else "under" if "under" in label else None
        if not side:
            continue
        parsed[side] = {"line": _extract_line(label), "odds": odd}
    return parsed


def _merge_period_market(target: dict[str, Any], market_name: str, payload: dict[str, Any]) -> None:
    if not payload:
        return
    lowered = str(market_name or "").lower()
    if any(token in lowered for token in ("1st half", "first half", "1h", "1º tempo", "1 tempo")):
        target.setdefault("first_half", {}).update(payload)
        return
    if any(token in lowered for token in ("2nd half", "second half", "2h", "2º tempo", "2 tempo")):
        target.setdefault("second_half", {}).update(payload)
        return
    target.update(payload)


def _parse_handicap_values(values: list[dict[str, Any]]) -> dict[str, dict[str, float | str | None]]:
    parsed: dict[str, dict[str, float | str | None]] = {}
    for value in values:
        label = str(value.get("value") or value.get("label") or "").lower()
        odd = _safe_float(value.get("odd") or value.get("odds"), None)
        if odd is None:
            continue
        side = (
            "home"
            if any(token in label for token in ("home", "1"))
            else "away"
            if any(token in label for token in ("away", "2"))
            else None
        )
        if not side:
            continue
        parsed[side] = {"line": _extract_line(label), "odds": odd}
    return parsed


def _parse_btts_values(values: list[dict[str, Any]]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        label = normalize_selection_name(value.get("value") or value.get("label"))
        odd = _safe_float(value.get("odd") or value.get("odds"), None)
        if odd is None:
            continue
        if label == "btts_yes":
            parsed["yes"] = odd
        elif label == "btts_no":
            parsed["no"] = odd
    return parsed


def _iter_bookmaker_bets(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    groups: list[tuple[str, dict[str, Any]]] = []
    for bookmaker in payload.get("bookmakers", []) or []:
        bookmaker_name = str(bookmaker.get("name") or bookmaker.get("id") or "bookmaker").strip()
        for bet in bookmaker.get("bets", []) or []:
            groups.append((bookmaker_name, bet))
    for bet in payload.get("odds", []) or []:
        groups.append(("live", bet))
    for bet in payload.get("bets", []) or []:
        groups.append(("unknown", bet))
    return groups


def _normalized_market_count(payload: dict[str, Any]) -> int:
    count = 0
    for key, value in payload.items():
        if key in {"summary", "_meta"}:
            continue
        if isinstance(value, dict):
            for child in value.values():
                if isinstance(child, dict):
                    count += sum(1 for grandchild in child.values() if grandchild not in (None, "", {}))
                elif child not in (None, "", {}):
                    count += 1
    return count


def _parse_markets(payload: dict[str, Any], *, home_team: Any = "", away_team: Any = "") -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for _, bet in _iter_bookmaker_bets(payload or {}):
        bet_name = str(bet.get("name") or bet.get("label") or "")
        canonical = normalize_market_name(bet_name)
        values = bet.get("values", []) or []
        if canonical == "1X2":
            parsed["1x2"] = _parse_1x2_values(values, home_team, away_team)
        elif canonical == "BTTS":
            parsed["btts"] = _parse_btts_values(values)
        elif canonical == "OVER_UNDER":
            target = parsed.setdefault("goals", {})
            _merge_period_market(target, bet_name, _parse_total_values(values))
        elif canonical == "ASIAN_HANDICAP":
            parsed.setdefault("asian", {}).update(_parse_handicap_values(values))
        elif canonical == "CORNERS":
            target = parsed.setdefault("corners", {})
            _merge_period_market(target, bet_name, _parse_total_values(values))
        elif canonical == "CARDS":
            target = parsed.setdefault("cards", {})
            _merge_period_market(target, bet_name, _parse_total_values(values))
    return {key: value for key, value in parsed.items() if value}


@dataclass
class ApiFootballStatus:
    configured: bool = False
    active: bool = False
    fallback_active: bool = False
    last_success_at: str | None = None
    last_live_update_at: str | None = None
    last_error: str | None = None
    recent_errors: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    requests_total: int = 0
    requests_used_today: int = 0
    rate_limit_remaining: int | None = None
    rate_limit_limit: int | None = None
    rate_limit_reset: str | None = None
    last_http_status: int | None = None
    last_payload_items: int = 0
    last_cache_key: str | None = None

    def as_dict(self, *, limiter: ProviderRateLimiter | None = None, max_rpm: int = 20) -> dict[str, Any]:
        limiter = limiter or get_provider_limiter()
        rate_state = limiter.status("api_football", max_rpm)
        return {
            "configured": self.configured,
            "active": self.active,
            "fallback_active": self.fallback_active,
            "last_success_at": self.last_success_at,
            "last_live_update_at": self.last_live_update_at,
            "last_error": self.last_error,
            "recent_errors": list(self.recent_errors),
            "requests_total": self.requests_total,
            "requests_used_today": self.requests_used_today,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_limit": self.rate_limit_limit,
            "rate_limit_reset": self.rate_limit_reset,
            "last_http_status": self.last_http_status,
            "last_payload_items": self.last_payload_items,
            "last_cache_key": self.last_cache_key,
            "cooldown_active": bool(not rate_state.allowed and rate_state.cooling_down),
            "cooldown_seconds": rate_state.wait_seconds if not rate_state.allowed else 0,
            "cooldown_reason": rate_state.reason or "",
        }


class ApiFootballProvider:
    provider_name = "api_football"

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        *,
        timeout_seconds: int = 5,
        max_rpm: int = 20,
        cooldown_seconds: int = 60,
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
        cache: TTLCache | None = None,
        limiter: ProviderRateLimiter | None = None,
        client_factory: Callable[[int], Any] | None = None,
        sleep_func: Callable[[float], Any] | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip() or None
        self.base_url = str(base_url or "https://v3.football.api-sports.io").rstrip("/")
        self.timeout_seconds = max(2, int(timeout_seconds or 5))
        self.max_rpm = max(1, int(max_rpm or 20))
        self.cooldown_seconds = max(15, int(cooldown_seconds or 60))
        self.usage_tracker = usage_tracker
        self.cost_per_request_brl = float(cost_per_request_brl or 0.0)
        self.cache = cache or get_runtime_cache()
        self.limiter = limiter or get_provider_limiter()
        self.client_factory = client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))
        self.sleep_func = sleep_func or asyncio.sleep
        self.status = ApiFootballStatus(configured=bool(self.api_key))
        self._lock = threading.Lock()

    def headers(self) -> dict[str, str]:
        return {"x-apisports-key": str(self.api_key or "")}

    def _request_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
        return f"{self.base_url}{path}{'?' + query if query else ''}"

    def status_snapshot(self) -> dict[str, Any]:
        usage_today = self._usage_today()
        if usage_today:
            self.status.requests_used_today = int(usage_today.get("requests", 0) or 0)
        return self.status.as_dict(limiter=self.limiter, max_rpm=self.max_rpm)

    async def health_check(self) -> dict[str, Any]:
        try:
            data = await self.get_leagues()
        except Exception as exc:
            return {
                "ok": False,
                "message": sanitize_text(str(exc), self.api_key),
                "status": self.status_snapshot(),
            }
        return {
            "ok": True,
            "message": f"API-Football pronta com {len(data)} ligas em cache/backend.",
            "status": self.status_snapshot(),
        }

    async def get_leagues(self) -> list[dict[str, Any]]:
        payload = await self._request(
            "/leagues",
            params={"current": "true"},
            cache_key="api-football:leagues",
            ttl_seconds=24 * 60 * 60,
        )
        return [self._normalize_league(item) for item in payload]

    async def get_live_fixtures(self) -> list[dict[str, Any]]:
        fixtures_payload = await self._request(
            "/fixtures",
            params={"live": "all"},
            cache_key="api-football:fixtures:live",
            ttl_seconds=30,
            stale_seconds=120,
        )
        odds_map = await self._get_live_odds_map()
        normalized: list[dict[str, Any]] = []
        fallback_odds_requests = 0
        for item in fixtures_payload:
            fixture = item.get("fixture") or {}
            league = item.get("league") or {}
            teams = item.get("teams") or {}
            fixture_id = str(fixture.get("id") or "").strip()
            odds = odds_map.get(fixture_id) or {}
            if not odds and fixture_id and fallback_odds_requests < 3:
                fallback_odds_requests += 1
                try:
                    odds_result = await self.get_odds_by_fixture_or_fallback(
                        fixture_id,
                        league_id=league.get("id"),
                        season=league.get("season"),
                        fixture_date=str(fixture.get("date") or "")[:10],
                        home_team=(teams.get("home") or {}).get("name"),
                        away_team=(teams.get("away") or {}).get("name"),
                    )
                    odds = odds_result.get("odds") or {}
                except Exception as exc:
                    logger.info(
                        "[ODDS] fixture_id=%s fallback failed=%s",
                        fixture_id,
                        sanitize_text(str(exc), self.api_key),
                    )
            normalized.append(self.normalize_fixture(item, odds=odds))
        with self._lock:
            self.status.last_live_update_at = _now_iso()
            self.status.last_payload_items = len(normalized)
        return normalized

    async def get_fixtures_by_date(self, fixture_date: str | date) -> list[dict[str, Any]]:
        if isinstance(fixture_date, date):
            fixture_date = fixture_date.isoformat()
        payload = await self._request(
            "/fixtures",
            params={"date": str(fixture_date)},
            cache_key=f"api-football:fixtures:date:{fixture_date}",
            ttl_seconds=10 * 60,
            stale_seconds=60 * 60,
        )
        return [self.normalize_fixture(item) for item in payload]

    async def get_fixture_statistics(self, fixture_id: str | int) -> dict[str, Any]:
        payload = await self._request(
            "/fixtures/statistics",
            params={"fixture": str(fixture_id)},
            cache_key=f"api-football:stats:{fixture_id}",
            ttl_seconds=60,
            stale_seconds=5 * 60,
        )
        return self.normalize_stats(payload)

    async def get_fixture_events(self, fixture_id: str | int) -> list[dict[str, Any]]:
        payload = await self._request(
            "/fixtures/events",
            params={"fixture": str(fixture_id)},
            cache_key=f"api-football:events:{fixture_id}",
            ttl_seconds=60,
            stale_seconds=5 * 60,
        )
        return [self._normalize_event(item) for item in payload]

    async def get_fixture_lineups(self, fixture_id: str | int) -> list[dict[str, Any]]:
        payload = await self._request(
            "/fixtures/lineups",
            params={"fixture": str(fixture_id)},
            cache_key=f"api-football:lineups:{fixture_id}",
            ttl_seconds=10 * 60,
            stale_seconds=60 * 60,
        )
        return [self._normalize_lineup(item) for item in payload]

    async def get_fixture_odds(self, fixture_id: str | int) -> dict[str, Any]:
        odds_result = await self.get_odds_by_fixture_or_fallback(fixture_id)
        return odds_result.get("odds") or {}

    async def get_odds_by_fixture_or_fallback(
        self,
        fixture_id: str | int,
        league_id: str | int | None = None,
        season: str | int | None = None,
        fixture_date: str | date | None = None,
        *,
        home_team: Any = "",
        away_team: Any = "",
    ) -> dict[str, Any]:
        fixture_id_str = str(fixture_id or "").strip()
        logger.info("[ODDS] fixture_id=%s request started", fixture_id_str)
        payload = await self._request(
            "/odds",
            params={"fixture": fixture_id_str},
            cache_key=f"api-football:odds:{fixture_id_str}",
            ttl_seconds=60,
            stale_seconds=10 * 60,
        )
        logger.info("[ODDS] fixture_id=%s raw count=%s", fixture_id_str, len(payload or []))
        source = "fixture"
        if not payload and league_id and season:
            if isinstance(fixture_date, date):
                fixture_date = fixture_date.isoformat()
            params: dict[str, Any] = {"league": str(league_id), "season": str(season)}
            if fixture_date:
                params["date"] = str(fixture_date)
            logger.info("[ODDS] fixture_id=%s fallback league/date started", fixture_id_str)
            fallback_payload = await self._request(
                "/odds",
                params=params,
                cache_key=f"api-football:odds:league:{league_id}:{season}:{fixture_date or 'all'}",
                ttl_seconds=60,
                stale_seconds=10 * 60,
            )
            payload = [
                item
                for item in fallback_payload
                if str((item.get("fixture") or {}).get("id") or "").strip() == fixture_id_str
            ]
            source = "league_date"
            logger.info("[ODDS] fixture_id=%s fallback filtered raw count=%s", fixture_id_str, len(payload or []))

        response = payload[0] if payload else {}
        detailed = self.normalize_odds_detailed(response, home_team=home_team, away_team=away_team)
        normalized = detailed.get("normalized") or {}
        normalized_count = int(detailed.get("normalized_count") or 0)
        reason = detailed.get("diagnosis") or "Odds confirmadas."
        if not payload:
            reason = "API retornou 0 odds para este fixture."
        logger.info("[ODDS] fixture_id=%s normalized count=%s", fixture_id_str, normalized_count)
        if detailed.get("unsupported_markets"):
            logger.info("[ODDS] fixture_id=%s unsupported markets=%s", fixture_id_str, detailed.get("unsupported_markets"))
        if not normalized_count:
            logger.info("[ODDS] fixture_id=%s unavailable reason=%s", fixture_id_str, reason)
        return {
            "fixture_id": fixture_id_str,
            "source": source,
            "odds": normalized,
            "raw": payload,
            "raw_count": len(payload or []),
            "normalized_count": normalized_count,
            "unsupported_markets": detailed.get("unsupported_markets") or [],
            "markets_found": detailed.get("markets_found") or [],
            "bookmakers": detailed.get("bookmakers") or [],
            "odds_unavailable": normalized_count <= 0,
            "reason": reason,
        }

    async def odds_debug(self) -> dict[str, Any]:
        debug = await self._debug_request(
            "/odds/live",
            params={},
            cache_key="api-football:odds:live:debug",
            ttl_seconds=30,
        )
        payload = debug.get("response") or []
        first = payload[0] if payload else {}
        detailed = self.normalize_odds_detailed(first)
        errors = list(debug.get("errors") or []) + list(detailed.get("errors") or [])
        return {
            "api_key_configured": bool(self.api_key),
            "provider": "api-football",
            "last_request_url": debug.get("request_url"),
            "last_status_code": debug.get("status_code"),
            "raw_response_count": len(payload),
            "normalized_count": detailed.get("normalized_count") or 0,
            "sample_raw": first,
            "sample_normalized": detailed.get("normalized") or {},
            "markets_found": detailed.get("markets_found") or [],
            "bookmakers": detailed.get("bookmakers") or [],
            "unsupported_markets": detailed.get("unsupported_markets") or [],
            "errors": errors,
            "diagnosis": self._diagnose_odds(
                status_code=debug.get("status_code"),
                raw_count=len(payload),
                normalized_count=int(detailed.get("normalized_count") or 0),
                errors=errors,
                unsupported_markets=detailed.get("unsupported_markets") or [],
                endpoint="/odds/live",
            ),
        }

    async def fixture_odds_debug(
        self,
        fixture_id: str | int,
        league_id: str | int | None = None,
        season: str | int | None = None,
        fixture_date: str | date | None = None,
    ) -> dict[str, Any]:
        fixture_id_str = str(fixture_id or "").strip()
        first_debug = await self._debug_request(
            "/odds",
            params={"fixture": fixture_id_str},
            cache_key=f"api-football:odds:debug:{fixture_id_str}",
            ttl_seconds=60,
        )
        payload = first_debug.get("response") or []
        fallback_debug: dict[str, Any] | None = None
        if not payload and league_id and season:
            if isinstance(fixture_date, date):
                fixture_date = fixture_date.isoformat()
            params: dict[str, Any] = {"league": str(league_id), "season": str(season)}
            if fixture_date:
                params["date"] = str(fixture_date)
            fallback_debug = await self._debug_request(
                "/odds",
                params=params,
                cache_key=f"api-football:odds:debug:league:{league_id}:{season}:{fixture_date or 'all'}",
                ttl_seconds=60,
            )
            payload = [
                item
                for item in fallback_debug.get("response") or []
                if str((item.get("fixture") or {}).get("id") or "").strip() == fixture_id_str
            ]
        first = payload[0] if payload else {}
        home_team = ((first.get("teams") or {}).get("home") or {}).get("name") if isinstance(first, dict) else ""
        away_team = ((first.get("teams") or {}).get("away") or {}).get("name") if isinstance(first, dict) else ""
        detailed = self.normalize_odds_detailed(first, home_team=home_team, away_team=away_team)
        errors = list(first_debug.get("errors") or [])
        if fallback_debug:
            errors.extend(fallback_debug.get("errors") or [])
        errors.extend(detailed.get("errors") or [])
        status_code = (
            first_debug.get("status_code")
            if payload or not fallback_debug
            else fallback_debug.get("status_code")
        )
        return {
            "fixture_id": fixture_id_str,
            "provider": "api-football",
            "api_key_configured": bool(self.api_key),
            "request_url": first_debug.get("request_url"),
            "fallback_request_url": (fallback_debug or {}).get("request_url"),
            "status": status_code,
            "raw_count": len(payload),
            "normalized_count": detailed.get("normalized_count") or 0,
            "markets_found": detailed.get("markets_found") or [],
            "bookmakers_found": detailed.get("bookmakers") or [],
            "unsupported_markets": detailed.get("unsupported_markets") or [],
            "sample_raw": first,
            "sample_normalized": detailed.get("normalized") or {},
            "errors": errors,
            "diagnosis": self._diagnose_odds(
                status_code=status_code,
                raw_count=len(payload),
                normalized_count=int(detailed.get("normalized_count") or 0),
                errors=errors,
                unsupported_markets=detailed.get("unsupported_markets") or [],
                endpoint="/odds?fixture=...",
            ),
        }

    def normalize_fixture(self, payload: dict[str, Any], *, odds: dict[str, Any] | None = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
        fixture = payload.get("fixture", {}) or {}
        status = fixture.get("status", {}) or {}
        teams = payload.get("teams", {}) or {}
        league = payload.get("league", {}) or {}
        goals = payload.get("goals", {}) or {}
        score = payload.get("score", {}) or {}
        fixture_id = str(fixture.get("id") or "").strip()
        normalized_odds = odds or {}
        one_x_two = normalized_odds.get("1x2") if isinstance(normalized_odds, dict) else {}
        odds_meta = normalized_odds.get("_meta", {}) if isinstance(normalized_odds, dict) else {}
        odds_confirmed = bool(
            odds_meta.get("confirmed")
            or odds_meta.get("normalized_count")
            or _safe_float((one_x_two or {}).get("home"), None)
            or _safe_float((one_x_two or {}).get("draw"), None)
            or _safe_float((one_x_two or {}).get("away"), None)
        )
        home_stat = (stats or {}).get("home", {})
        away_stat = (stats or {}).get("away", {})
        minute = _safe_int(status.get("elapsed"))
        return {
            "fixture_id": fixture_id,
            "game_id": fixture_id,
            "league_id": league.get("id"),
            "season": league.get("season"),
            "fixture_date": str(fixture.get("date") or "")[:10] or None,
            "league": str(league.get("name") or "Unknown league"),
            "division": str(league.get("name") or "Outras ligas"),
            "country": str(league.get("country") or "").strip(),
            "home_team": str((teams.get("home") or {}).get("name") or "Home"),
            "away_team": str((teams.get("away") or {}).get("name") or "Away"),
            "home": str((teams.get("home") or {}).get("name") or "Home"),
            "away": str((teams.get("away") or {}).get("name") or "Away"),
            "minute": minute,
            "status": str(status.get("long") or status.get("short") or "").strip() or None,
            "state": "in" if minute > 0 else str(status.get("short") or "").strip().lower() or None,
            "score_home": _safe_int(goals.get("home")),
            "score_away": _safe_int(goals.get("away")),
            "home_goals": _safe_int(goals.get("home")),
            "away_goals": _safe_int(goals.get("away")),
            "scoreline": f"{_safe_int(goals.get('home'))} x {_safe_int(goals.get('away'))}",
            "minute_label": f"{minute}'" if minute > 0 else str(status.get("short") or "PRE"),
            "kickoff_at": str(fixture.get("date") or "").strip() or None,
            "venue": str((fixture.get("venue") or {}).get("name") or "").strip() or None,
            "odds": normalized_odds,
            "odds_confirmed": odds_confirmed,
            "odds_status": "confirmed" if odds_confirmed else "unavailable",
            "odds_reason": (
                str(odds_meta.get("diagnosis") or "Odds reais confirmadas.")
                if odds_confirmed
                else str(odds_meta.get("diagnosis") or "Sem odds reais confirmadas. Entrada bloqueada.")
            ),
            "stats": stats or {},
            "events": payload.get("events") or [],
            "lineups": payload.get("lineups") or [],
            "source": "api-football",
            "odds_home": _safe_float((one_x_two or {}).get("home"), None),
            "odds_draw": _safe_float((one_x_two or {}).get("draw"), None),
            "odds_away": _safe_float((one_x_two or {}).get("away"), None),
            "home_pressure": _safe_int(home_stat.get("pressure_index")),
            "away_pressure": _safe_int(away_stat.get("pressure_index")),
            "home_shots_on": _safe_int(home_stat.get("shots_on")),
            "away_shots_on": _safe_int(away_stat.get("shots_on")),
            "period_scores": {
                "halftime": score.get("halftime") or {},
                "fulltime": score.get("fulltime") or {},
            },
        }

    def normalize_stats(self, payload: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
        rows = payload if isinstance(payload, list) else payload.get("response") or []
        data = {"home": {}, "away": {}}
        for item in rows[:2]:
            team_name = str((item.get("team") or {}).get("name") or "").strip()
            side = "home" if not data["home"] else "away"
            if data["home"] and data["away"]:
                break
            stats_map: dict[str, Any] = {}
            for stat in item.get("statistics", []) or []:
                stats_map[str(stat.get("type") or "")] = stat.get("value")
            pressure_index = (
                (_safe_float(stats_map.get("Shots on Goal"), 0.0) or 0.0) * 2.5
                + (_safe_float(stats_map.get("Total Shots"), 0.0) or 0.0) * 1.2
                + (_safe_float(stats_map.get("Dangerous Attacks"), 0.0) or 0.0) * 0.8
                + (_safe_float(stats_map.get("Corner Kicks"), 0.0) or 0.0) * 1.0
                + (_safe_float(stats_map.get("Ball Possession"), 0.0) or 0.0) * 0.15
            )
            data[side] = {
                "team": team_name,
                "possession": _safe_float(stats_map.get("Ball Possession"), 0.0),
                "shots": _safe_int(stats_map.get("Total Shots")),
                "shots_on": _safe_int(stats_map.get("Shots on Goal")),
                "corners": _safe_int(stats_map.get("Corner Kicks")),
                "yellow": _safe_int(stats_map.get("Yellow Cards")),
                "red": _safe_int(stats_map.get("Red Cards")),
                "dangerous_attacks": _safe_int(stats_map.get("Dangerous Attacks")),
                "attacks": _safe_int(stats_map.get("Attacks")),
                "xg": _safe_float(stats_map.get("Expected Goals"), None),
                "pressure_index": round(pressure_index, 2),
                "raw": stats_map,
            }
        return data

    def normalize_odds_detailed(self, payload: dict[str, Any], *, home_team: Any = "", away_team: Any = "") -> dict[str, Any]:
        payload = payload or {}
        markets_found: list[str] = []
        bookmakers: list[str] = []
        unsupported: list[dict[str, Any]] = []
        errors: list[str] = []
        raw_value_count = 0
        for bookmaker_name, bet in _iter_bookmaker_bets(payload):
            if bookmaker_name and bookmaker_name not in bookmakers:
                bookmakers.append(bookmaker_name)
            bet_name = str(bet.get("name") or bet.get("label") or "").strip() or "unknown"
            markets_found.append(bet_name)
            values = bet.get("values", []) or []
            raw_value_count += len(values)
            if normalize_market_name(bet_name) == "UNSUPPORTED":
                unsupported.append(
                    {
                        "bookmaker": bookmaker_name,
                        "market": bet_name,
                        "values_count": len(values),
                    }
                )
        parsed = _parse_markets(payload, home_team=home_team, away_team=away_team)
        one_x_two = parsed.get("1x2") or {}
        normalized_count = _normalized_market_count(parsed)
        diagnosis = self._diagnose_odds(
            status_code=self.status.last_http_status,
            raw_count=1 if payload else 0,
            normalized_count=normalized_count,
            errors=errors,
            unsupported_markets=unsupported,
            endpoint="normalizer",
        )
        normalized = {
            **parsed,
            "summary": {
                "home": _safe_float(one_x_two.get("home"), None),
                "draw": _safe_float(one_x_two.get("draw"), None),
                "away": _safe_float(one_x_two.get("away"), None),
            },
            "_meta": {
                "confirmed": normalized_count > 0,
                "raw_value_count": raw_value_count,
                "normalized_count": normalized_count,
                "markets_found": markets_found,
                "bookmakers": bookmakers,
                "unsupported_markets": unsupported,
                "diagnosis": diagnosis,
            },
        }
        return {
            "normalized": normalized,
            "raw": payload,
            "raw_count": 1 if payload else 0,
            "raw_value_count": raw_value_count,
            "normalized_count": normalized_count,
            "markets_found": markets_found,
            "bookmakers": bookmakers,
            "unsupported_markets": unsupported,
            "errors": errors,
            "diagnosis": diagnosis,
        }

    def normalize_odds(self, payload: dict[str, Any], *, home_team: Any = "", away_team: Any = "") -> dict[str, Any]:
        detailed = self.normalize_odds_detailed(payload or {}, home_team=home_team, away_team=away_team)
        return detailed.get("normalized") or {}

    async def _get_live_odds_map(self) -> dict[str, dict[str, Any]]:
        try:
            payload = await self._request(
                "/odds/live",
                cache_key="api-football:odds:live",
                ttl_seconds=60,
                stale_seconds=10 * 60,
            )
        except Exception:
            return {}
        by_fixture: dict[str, dict[str, Any]] = {}
        for item in payload:
            fixture_id = str(
                (item.get("fixture") or {}).get("id")
                or item.get("fixture")
                or item.get("id")
                or ""
            ).strip()
            if not fixture_id:
                continue
            teams = item.get("teams") or {}
            by_fixture[fixture_id] = self.normalize_odds(
                item,
                home_team=(teams.get("home") or {}).get("name"),
                away_team=(teams.get("away") or {}).get("name"),
            )
        return by_fixture

    def _normalize_league(self, payload: dict[str, Any]) -> dict[str, Any]:
        league = payload.get("league", {}) or {}
        country = payload.get("country", {}) or {}
        seasons = payload.get("seasons", []) or []
        return {
            "league_id": league.get("id"),
            "name": league.get("name"),
            "type": league.get("type"),
            "logo": league.get("logo"),
            "country": country.get("name"),
            "country_code": country.get("code"),
            "season_current": bool(seasons[-1].get("current")) if seasons else False,
            "source": "api-football",
        }

    def _normalize_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        time_row = payload.get("time", {}) or {}
        team_row = payload.get("team", {}) or {}
        player_row = payload.get("player", {}) or {}
        assist_row = payload.get("assist", {}) or {}
        return {
            "minute": _safe_int(time_row.get("elapsed")),
            "extra": _safe_int(time_row.get("extra"), 0),
            "team": team_row.get("name"),
            "player": player_row.get("name"),
            "assist": assist_row.get("name"),
            "type": payload.get("type"),
            "detail": payload.get("detail"),
            "comments": payload.get("comments"),
            "source": "api-football",
        }

    def _normalize_lineup(self, payload: dict[str, Any]) -> dict[str, Any]:
        team_row = payload.get("team", {}) or {}
        coach_row = payload.get("coach", {}) or {}
        return {
            "team": team_row.get("name"),
            "formation": payload.get("formation"),
            "coach": coach_row.get("name"),
            "startXI": [item.get("player") or {} for item in payload.get("startXI", []) or []],
            "substitutes": [item.get("player") or {} for item in payload.get("substitutes", []) or []],
            "source": "api-football",
        }

    def _diagnose_odds(
        self,
        *,
        status_code: Any,
        raw_count: int,
        normalized_count: int,
        errors: list[Any] | None = None,
        unsupported_markets: list[Any] | None = None,
        endpoint: str = "",
    ) -> str:
        errors = errors or []
        unsupported_markets = unsupported_markets or []
        if not self.api_key:
            return "API-Football sem API_FOOTBALL_KEY configurada."
        if status_code == 401:
            return "API-Football retornou 401: chave invalida ou revogada."
        if status_code == 403:
            return "API-Football retornou 403: plano/permissao pode nao incluir odds."
        if status_code == 429:
            return "API-Football retornou 429: limite de requests atingido; usando cache/fallback quando existir."
        if isinstance(status_code, int) and status_code >= 500:
            return "API-Football retornou erro interno; tentar novamente depois."
        if errors:
            return f"API retornou erro para odds: {sanitize_text(str(errors[0]), self.api_key)}"
        if raw_count <= 0:
            if endpoint == "/odds/live":
                return "API retornou 0 odds ao vivo; pode nao haver odds live no plano atual ou nos jogos em andamento."
            return "API retornou 0 odds para o fixture/periodo consultado."
        if normalized_count <= 0 and unsupported_markets:
            return "API retornou odds, mas nenhum mercado reconhecido pelo normalizador; mercados preservados como unsupported_market."
        if normalized_count <= 0:
            return "API retornou payload de odds sem valores reconheciveis."
        return "Odds reais confirmadas e normalizadas."

    async def _debug_request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None,
        cache_key: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        request_url = self._request_url(path, params)
        cached = self.cache.get(cache_key)
        if cached and isinstance(cached.value, list):
            return {
                "ok": True,
                "status_code": self.status.last_http_status,
                "request_url": request_url,
                "response": cached.value,
                "errors": [],
                "cached": True,
            }
        if not self.api_key:
            return {
                "ok": False,
                "status_code": None,
                "request_url": request_url,
                "response": [],
                "errors": ["API_FOOTBALL_KEY ausente"],
                "cached": False,
            }
        decision = self.limiter.acquire(self.provider_name, self.max_rpm)
        if not decision.allowed:
            stale = self.cache.get(cache_key, allow_stale=True)
            return {
                "ok": bool(stale and isinstance(stale.value, list)),
                "status_code": self.status.last_http_status,
                "request_url": request_url,
                "response": stale.value if stale and isinstance(stale.value, list) else [],
                "errors": [decision.reason or "API-Football em cooldown local"],
                "cached": bool(stale and isinstance(stale.value, list)),
            }
        try:
            async with self.client_factory(self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}{path}",
                    params=params or {},
                    headers=self.headers(),
                )
            with self._lock:
                self.status.last_http_status = int(response.status_code)
            self._track_headers(response.headers)
            body = response.json()
            payload = body.get("response", body)
            if not isinstance(payload, list):
                payload = [payload] if payload else []
            errors = _body_errors(body)
            if response.status_code == 429:
                wait_seconds = retry_after_seconds(response.headers.get("Retry-After")) or self.cooldown_seconds
                self.limiter.cooldown(self.provider_name, wait_seconds, reason="API-Football 429")
            ok = response.status_code < 400 and not errors
            if ok:
                self.cache.set(cache_key, payload, ttl_seconds, stale_seconds=max(ttl_seconds * 10, ttl_seconds))
                self._record_success(cache_key, len(payload), len(response.content))
            else:
                self._record_error(
                    self._diagnose_odds(
                        status_code=response.status_code,
                        raw_count=len(payload),
                        normalized_count=0,
                        errors=errors,
                        unsupported_markets=[],
                        endpoint=path,
                    )
                )
            return {
                "ok": ok,
                "status_code": int(response.status_code),
                "request_url": request_url,
                "response": payload,
                "errors": [sanitize_text(str(item), self.api_key) for item in errors],
                "cached": False,
            }
        except httpx.HTTPError as exc:
            message = sanitize_text(str(exc), self.api_key)
            self._record_error(f"Falha HTTP API-Football odds debug: {message}")
            stale = self.cache.get(cache_key, allow_stale=True)
            return {
                "ok": False,
                "status_code": self.status.last_http_status,
                "request_url": request_url,
                "response": stale.value if stale and isinstance(stale.value, list) else [],
                "errors": [message],
                "cached": bool(stale and isinstance(stale.value, list)),
            }

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        cache_key: str,
        ttl_seconds: int,
        stale_seconds: int | None = None,
    ) -> list[dict[str, Any]]:
        cache_key = str(cache_key)
        cached = self.cache.get(cache_key)
        if cached and isinstance(cached.value, list):
            with self._lock:
                self.status.active = True
                self.status.fallback_active = False
                self.status.last_cache_key = cache_key
            return cached.value
        stale = self.cache.get(cache_key, allow_stale=True)
        if not self.api_key:
            if stale and isinstance(stale.value, list):
                self._note_fallback("API-Football sem chave configurada; usando cache antigo.")
                return stale.value
            raise RuntimeError("API-Football sem API_FOOTBALL_KEY configurada.")

        decision = self.limiter.acquire(self.provider_name, self.max_rpm)
        if not decision.allowed:
            if stale and isinstance(stale.value, list):
                self._note_fallback(
                    f"API-Football em cooldown/rate limit local ({decision.reason}); usando fallback."
                )
                return stale.value
            raise RuntimeError(decision.reason or "API-Football em cooldown local.")

        attempt_waits = [2, 5, 10, 30]
        for attempt, default_wait in enumerate(attempt_waits, start=1):
            try:
                async with self.client_factory(self.timeout_seconds) as client:
                    response = await client.get(
                        f"{self.base_url}{path}",
                        params=params or {},
                        headers=self.headers(),
                    )
                with self._lock:
                    self.status.last_http_status = int(response.status_code)
                self._track_headers(response.headers)
                if response.status_code == 401:
                    message = "API-Football retornou 401 (chave invalida ou revogada)."
                    self._record_error(message)
                    raise RuntimeError(message)
                if response.status_code == 403:
                    message = "API-Football retornou 403 (plano/permissao insuficiente)."
                    self._record_error(message)
                    raise RuntimeError(message)
                if response.status_code == 429:
                    if stale and isinstance(stale.value, list):
                        wait_seconds = retry_after_seconds(response.headers.get("Retry-After")) or self.cooldown_seconds
                        self.limiter.cooldown(
                            self.provider_name,
                            wait_seconds,
                            reason="API-Football 429",
                        )
                        self._note_fallback(
                            f"API-Football em limite ({wait_seconds}s); usando fallback em cache."
                        )
                        return stale.value
                    wait_seconds = retry_after_seconds(response.headers.get("Retry-After")) or default_wait
                    if attempt == len(attempt_waits):
                        self.limiter.cooldown(
                            self.provider_name,
                            self.cooldown_seconds,
                            reason="API-Football 429",
                        )
                        self._record_error("API-Football entrou em cooldown por 429.")
                        raise RuntimeError("API-Football limitou requests (429).")
                    await self.sleep_func(wait_seconds)
                    continue
                if response.status_code >= 500:
                    message = f"API-Football indisponivel ({response.status_code})."
                    if stale and isinstance(stale.value, list):
                        self._note_fallback(f"{message} Usando fallback em cache.")
                        return stale.value
                    self._record_error(message)
                    raise RuntimeError(message)
                response.raise_for_status()
                body = response.json()
                payload = body.get("response", body)
                if not isinstance(payload, list):
                    payload = [payload] if payload else []
                api_errors = _body_errors(body)
                if api_errors:
                    message = f"API-Football retornou erro: {api_errors[0]}"
                    if stale and isinstance(stale.value, list):
                        self._note_fallback(f"{message}. Usando fallback em cache.")
                        return stale.value
                    self._record_error(message)
                    raise RuntimeError(sanitize_text(message, self.api_key))
                self.cache.set(
                    cache_key,
                    payload,
                    ttl_seconds,
                    stale_seconds=stale_seconds or max(ttl_seconds * 6, ttl_seconds),
                )
                self._record_success(cache_key, len(payload), len(response.content))
                return payload
            except httpx.HTTPError as exc:
                if stale and isinstance(stale.value, list):
                    self._note_fallback(
                        f"Falha HTTP na API-Football; usando fallback ({sanitize_text(str(exc), self.api_key)})."
                    )
                    return stale.value
                self._record_error(f"Falha HTTP API-Football: {sanitize_text(str(exc), self.api_key)}")
                raise RuntimeError("Falha HTTP na API-Football.") from exc

        if stale and isinstance(stale.value, list):
            self._note_fallback("API-Football instavel; usando fallback antigo.")
            return stale.value
        raise RuntimeError("API-Football indisponivel no momento.")

    def _usage_today(self) -> dict[str, Any] | None:
        if not self.usage_tracker:
            return None
        try:
            summary = self.usage_tracker.summary()
        except Exception:
            return None
        for service in summary.get("services_today", []) or []:
            if str(service.get("service")) == self.provider_name:
                return service
        return None

    def _track_headers(self, headers: httpx.Headers) -> None:
        remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        limit = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        with self._lock:
            self.status.rate_limit_remaining = _safe_int(remaining, None) if remaining is not None else self.status.rate_limit_remaining
            self.status.rate_limit_limit = _safe_int(limit, None) if limit is not None else self.status.rate_limit_limit
            self.status.rate_limit_reset = str(reset).strip() if reset else self.status.rate_limit_reset

    def _track_usage(self, *, success: bool, operation: str, response_bytes: int = 0, error: str | None = None) -> None:
        if not self.usage_tracker:
            return
        self.usage_tracker.record(
            self.provider_name,
            category="api",
            request_count=1,
            success=success,
            response_bytes=response_bytes,
            estimated_cost_brl=self.cost_per_request_brl,
            operation=operation,
            error=sanitize_text(error, self.api_key) if error else None,
        )

    def _record_success(self, cache_key: str, payload_items: int, response_bytes: int) -> None:
        self._track_usage(success=True, operation=cache_key, response_bytes=response_bytes)
        with self._lock:
            self.status.configured = bool(self.api_key)
            self.status.active = True
            self.status.fallback_active = False
            self.status.last_success_at = _now_iso()
            self.status.last_error = None
            self.status.last_payload_items = payload_items
            self.status.last_cache_key = cache_key
            self.status.requests_total += 1
        usage_today = self._usage_today()
        if usage_today:
            with self._lock:
                self.status.requests_used_today = int(usage_today.get("requests", 0) or 0)

    def _record_error(self, message: str) -> None:
        clean = sanitize_text(message, self.api_key)
        self._track_usage(success=False, operation="error", error=clean)
        with self._lock:
            self.status.active = False
            self.status.last_error = clean
            self.status.recent_errors.appendleft(clean)
            self.status.requests_total += 1

    def _note_fallback(self, message: str) -> None:
        clean = sanitize_text(message, self.api_key)
        with self._lock:
            self.status.fallback_active = True
            self.status.active = False
            self.status.last_error = clean
            self.status.recent_errors.appendleft(clean)


_SHARED_PROVIDER_LOCK = threading.Lock()
_SHARED_PROVIDERS: dict[tuple[str, str, int, int], ApiFootballProvider] = {}


def get_shared_api_football_provider(
    api_key: str | None,
    base_url: str,
    *,
    max_rpm: int = 20,
    cooldown_seconds: int = 60,
    usage_tracker: UsageTracker | None = None,
    cost_per_request_brl: float = 0.0,
) -> ApiFootballProvider:
    key = (
        str(api_key or "").strip(),
        str(base_url or "").rstrip("/"),
        int(max_rpm or 20),
        int(cooldown_seconds or 60),
    )
    with _SHARED_PROVIDER_LOCK:
        provider = _SHARED_PROVIDERS.get(key)
        if provider is None:
            provider = ApiFootballProvider(
                api_key,
                base_url,
                max_rpm=max_rpm,
                cooldown_seconds=cooldown_seconds,
                usage_tracker=usage_tracker,
                cost_per_request_brl=cost_per_request_brl,
            )
            _SHARED_PROVIDERS[key] = provider
        else:
            provider.usage_tracker = usage_tracker or provider.usage_tracker
            provider.cost_per_request_brl = float(cost_per_request_brl or provider.cost_per_request_brl or 0.0)
        return provider
