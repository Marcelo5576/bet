from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import httpx

from src.config import Settings
from src.intelligence.source_catalog import source_memory_rows
from src.intelligence.source_scraper import scraped_source_memory_rows
from src.usage_metrics import UsageTracker

logger = logging.getLogger("betsignal.supabase")


class SupabaseSink:
    def __init__(
        self,
        url: str | None,
        service_role_key: str | None,
        usage_tracker: UsageTracker | None = None,
        cost_per_request_brl: float = 0.0,
    ):
        self.url = (url or "").rstrip("/")
        self.service_role_key = service_role_key or ""
        self.disabled_until: datetime | None = None
        self.disabled_reason: str | None = None
        self.usage_tracker = usage_tracker
        self.cost_per_request_brl = float(cost_per_request_brl or 0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "SupabaseSink":
        return cls(
            settings.supabase_url,
            settings.supabase_service_role_key,
            usage_tracker=UsageTracker(settings.usage_metrics_db_file),
            cost_per_request_brl=settings.supabase_cost_per_request_brl,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_role_key)

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.disabled_until and datetime.now(timezone.utc) < self.disabled_until:
            return False
        return True

    async def sync_games(self, games: list[Any]) -> None:
        if not self.available or not games:
            return
        rows = [_game_row(game) for game in games]
        await self._upsert("betsignal_games", rows, "game_id")

    async def sync_signals(self, signals: list[dict[str, Any]]) -> None:
        if not self.available or not signals:
            return
        embedded_games = [
            signal.get("game")
            for signal in signals
            if isinstance(signal, dict) and isinstance(signal.get("game"), dict)
        ]
        if embedded_games:
            games_by_id = {
                str(game.get("game_id")): game
                for game in embedded_games
                if game.get("game_id")
            }
            await self._upsert(
                "betsignal_games",
                [_game_row(game) for game in games_by_id.values()],
                "game_id",
            )
        rows = [_signal_row(signal) for signal in signals if signal]
        await self._upsert("betsignal_signals", rows, "signal_id")

    async def sync_signal(self, signal: dict[str, Any] | None) -> None:
        if signal:
            await self.sync_signals([signal])

    async def sync_ai_memory(self, history: list[dict[str, Any]]) -> None:
        if not self.available:
            return
        await self.sync_ai_sources()
        if not history:
            return
        rows = _memory_rows(history)
        await self._upsert("betsignal_ai_memory", rows, "memory_id")

    async def sync_ai_sources(self) -> None:
        if not self.available:
            return
        rows = source_memory_rows()
        try:
            rows.extend(await scraped_source_memory_rows())
        except Exception as exc:
            logger.info("Falha ao coletar fontes raspadas para IA: %s", exc)
        await self._upsert("betsignal_ai_memory", rows, "memory_id")

    async def sync_ai_skills(self, skills: list[dict[str, Any]]) -> None:
        if not self.available or not skills:
            return
        rows = [_skill_row(skill) for skill in skills]
        await self._upsert("betsignal_ai_skills", rows, "skill_id", disable_on_missing_table=False)

    async def sync_simulation_session(self, session: dict[str, Any] | None) -> None:
        if not self.available or not session:
            return
        row = _simulation_memory_row(session)
        await self._upsert("betsignal_ai_memory", [row], "memory_id")
        await self._upsert(
            "betsignal_simulations",
            [_simulation_row(session)],
            "simulation_id",
            disable_on_missing_table=False,
            warn_on_error=False,
        )

    async def fetch_ai_skills(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{self.url}/rest/v1/betsignal_ai_skills",
                    headers=self._headers(),
                    params={
                        "select": "skill_id,title,intent,keywords,answer,priority,active,payload,updated_at",
                        "active": "eq.true",
                        "order": "priority.asc,title.asc",
                        "limit": "50",
                    },
                )
                response.raise_for_status()
                self._track(
                    success=True,
                    operation="fetch_ai_skills",
                    response_bytes=len(response.content),
                )
                return response.json()
        except httpx.HTTPStatusError as exc:
            self._track(success=False, operation="fetch_ai_skills", error=exc)
            logger.warning("Falha ao consultar skills IA no Supabase: %s", _safe_http_error(exc))
            return []
        except Exception as exc:
            self._track(success=False, operation="fetch_ai_skills", error=exc)
            logger.warning("Falha ao consultar skills IA no Supabase: %s", exc)
            return []

    async def fetch_ai_context(self, active_signal: dict[str, Any] | None) -> dict[str, Any]:
        if not self.available:
            return {"enabled": False, "items": []}
        game = (active_signal or {}).get("game") or {}
        subjects = {
            game.get("league"),
            game.get("division"),
            game.get("home"),
            game.get("away"),
            (active_signal or {}).get("market"),
            (active_signal or {}).get("entry_market"),
        }
        subjects = {item for item in subjects if item}
        params = {
            "select": "scope,subject,sample_size,hit_rate,roi_units,profit_units,avg_confidence,avg_edge,notes,updated_at",
            "order": "sample_size.desc,updated_at.desc",
            "limit": "12",
        }
        if subjects:
            escaped = ",".join(f'"{str(item)}"' for item in subjects)
            params["subject"] = f"in.({escaped})"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{self.url}/rest/v1/betsignal_ai_memory",
                    headers=self._headers(),
                    params=params,
                )
                response.raise_for_status()
                self._track(
                    success=True,
                    operation="fetch_ai_context",
                    response_bytes=len(response.content),
                )
                items = response.json()
                source_response = await client.get(
                    f"{self.url}/rest/v1/betsignal_ai_memory",
                    headers=self._headers(),
                    params={
                        "select": "scope,subject,source,notes,payload,updated_at",
                        "scope": "eq.source_catalog",
                        "order": "payload->>priority.asc",
                        "limit": "10",
                    },
                )
                if source_response.status_code in {200, 206}:
                    self._track(
                        success=True,
                        operation="fetch_ai_sources",
                        response_bytes=len(source_response.content),
                    )
                    items.extend(source_response.json())
                return {"enabled": True, "items": items}
        except httpx.HTTPStatusError as exc:
            self._track(success=False, operation="fetch_ai_context", error=exc)
            self._maybe_disable(exc.response)
            logger.warning("Falha ao consultar memoria IA no Supabase: %s", exc)
            return {"enabled": True, "items": [], "error": _safe_http_error(exc)}
        except Exception as exc:
            self._track(success=False, operation="fetch_ai_context", error=exc)
            logger.warning("Falha ao consultar memoria IA no Supabase: %s", exc)
            return {"enabled": True, "items": [], "error": str(exc)}

    async def _upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        conflict_key: str,
        disable_on_missing_table: bool = True,
        warn_on_error: bool = True,
    ) -> None:
        if not rows:
            return
        if not self.available:
            return
        url = f"{self.url}/rest/v1/{table}"
        headers = self._headers() | {"Prefer": "resolution=merge-duplicates,return=minimal"}
        params = {"on_conflict": conflict_key}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(url, headers=headers, params=params, json=rows)
                response.raise_for_status()
                self._track(
                    success=True,
                    operation=f"upsert:{table}",
                    response_bytes=len(response.content),
                )
        except httpx.HTTPStatusError as exc:
            self._track(success=False, operation=f"upsert:{table}", error=exc)
            if disable_on_missing_table:
                self._maybe_disable(exc.response)
            if warn_on_error:
                logger.warning("Falha ao sincronizar Supabase/%s: %s", table, _safe_http_error(exc))
        except Exception as exc:
            self._track(success=False, operation=f"upsert:{table}", error=exc)
            if warn_on_error:
                logger.warning("Falha ao sincronizar Supabase/%s: %s", table, exc)

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def _maybe_disable(self, response: httpx.Response) -> None:
        if response.status_code != 404:
            return
        body = response.text[:300]
        if "Could not find the table" not in body:
            return
        self.disabled_until = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.disabled_reason = "tabelas ausentes no Supabase; rode supabase_schema.sql"

    def _track(
        self,
        *,
        success: bool,
        operation: str,
        response_bytes: int = 0,
        error: Exception | None = None,
    ) -> None:
        if not self.usage_tracker:
            return
        self.usage_tracker.record(
            "supabase",
            category="api",
            request_count=1,
            success=success,
            response_bytes=response_bytes,
            estimated_cost_brl=self.cost_per_request_brl,
            operation=operation,
            error=str(error)[:240] if error else None,
        )


def _game_row(game: Any) -> dict[str, Any]:
    data = asdict(game) if is_dataclass(game) else dict(game)
    return {
        "game_id": str(data.get("game_id")),
        "league": data.get("league"),
        "division": data.get("division"),
        "home": data.get("home"),
        "away": data.get("away"),
        "minute": data.get("minute"),
        "home_goals": data.get("home_goals"),
        "away_goals": data.get("away_goals"),
        "home_pressure": data.get("home_pressure"),
        "away_pressure": data.get("away_pressure"),
        "home_shots_on": data.get("home_shots_on"),
        "away_shots_on": data.get("away_shots_on"),
        "odds_home": data.get("odds_home"),
        "odds_draw": data.get("odds_draw"),
        "odds_away": data.get("odds_away"),
        "priority": data.get("priority"),
        "markets": data.get("markets") or {},
        "raw": data,
        "updated_at": _now(),
    }


def _signal_row(signal: dict[str, Any]) -> dict[str, Any]:
    game = signal.get("game") or {}
    signal_id = signal.get("signal_id") or f"{game.get('game_id', 'unknown')}-{signal.get('created_at', _now())}"
    return {
        "signal_id": str(signal_id),
        "game_id": str(game.get("game_id") or ""),
        "action": signal.get("action"),
        "team": signal.get("team"),
        "market": signal.get("market"),
        "confidence": signal.get("confidence"),
        "target_odds": signal.get("target_odds"),
        "estimated_probability": signal.get("estimated_probability"),
        "implied_probability": signal.get("implied_probability"),
        "value_edge": signal.get("value_edge"),
        "fair_odds": signal.get("fair_odds"),
        "data_quality": signal.get("data_quality"),
        "stake_units": signal.get("stake_units"),
        "stake_value": signal.get("stake_value"),
        "entered": bool(signal.get("entered")),
        "entered_at": signal.get("entered_at"),
        "entry_market": signal.get("entry_market"),
        "entry_value": signal.get("entry_value"),
        "entry_odds": signal.get("entry_odds"),
        "entry_notes": signal.get("entry_notes"),
        "outcome": signal.get("outcome", "open"),
        "profit_units": signal.get("profit_units"),
        "created_at": signal.get("created_at") or _now(),
        "finished_at": signal.get("finished_at"),
        "payload": signal,
        "updated_at": _now(),
    }


def _memory_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settled = [item for item in history if item.get("outcome") in {"win", "loss"}]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in settled:
        game = item.get("game") or {}
        candidates = [
            ("league", game.get("league")),
            ("division", game.get("division")),
            ("team", game.get("home")),
            ("team", game.get("away")),
            ("market", item.get("entry_market") or item.get("market")),
        ]
        for scope, subject in candidates:
            if subject:
                groups.setdefault((scope, str(subject)), []).append(item)

    rows = []
    for (scope, subject), items in groups.items():
        rows.append(_memory_row(scope, subject, items))
    return rows


def _skill_row(skill: dict[str, Any]) -> dict[str, Any]:
    keywords = skill.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [item.strip() for item in keywords.split(",") if item.strip()]
    return {
        "skill_id": str(skill.get("skill_id") or skill.get("id") or ""),
        "title": str(skill.get("title") or ""),
        "intent": str(skill.get("intent") or ""),
        "keywords": [str(item).strip().lower() for item in keywords if str(item).strip()],
        "answer": str(skill.get("answer") or ""),
        "priority": int(skill.get("priority") or 100),
        "active": bool(skill.get("active", True)),
        "payload": skill.get("payload") or {},
        "updated_at": _now(),
    }


def _simulation_memory_row(session: dict[str, Any]) -> dict[str, Any]:
    simulation_id = _simulation_id(session)
    total_games = int(_as_float(session.get("total_games"), 0) or 0)
    greens = int(_as_float(session.get("greens"), 0) or 0)
    reds = int(_as_float(session.get("reds"), 0) or 0)
    profit_units = round(_as_float(session.get("profit_units"), 0.0) or 0.0, 2)
    trigger = str(session.get("trigger") or "simulation")
    scope = str(session.get("scan_scope") or "live")
    return {
        "memory_id": f"simulation:{simulation_id}",
        "scope": "simulation_session",
        "subject": f"{trigger}:{scope}"[:240],
        "source": "betsignal_simulator",
        "sample_size": total_games,
        "hit_rate": session.get("hit_rate"),
        "roi_units": session.get("roi"),
        "profit_units": profit_units,
        "avg_confidence": session.get("avg_confidence"),
        "avg_edge": session.get("avg_edge"),
        "notes": (
            f"Simulacao {trigger}: {total_games} jogos, {greens} green, "
            f"{reds} red, lucro {profit_units}u. Inclui entrada, saida e dinamica do jogo."
        ),
        "payload": session,
        "updated_at": _now(),
    }


def _simulation_row(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "simulation_id": _simulation_id(session),
        "trigger": session.get("trigger") or "simulation",
        "scan_scope": session.get("scan_scope"),
        "source_games": session.get("source_games"),
        "total_games": session.get("total_games"),
        "greens": session.get("greens"),
        "reds": session.get("reds"),
        "hit_rate": session.get("hit_rate"),
        "profit_units": session.get("profit_units"),
        "roi": session.get("roi"),
        "max_drawdown": session.get("max_drawdown"),
        "payload": session,
        "created_at": session.get("created_at") or _now(),
        "updated_at": _now(),
    }


def _simulation_id(session: dict[str, Any]) -> str:
    raw = session.get("simulation_id") or session.get("created_at") or _now()
    return str(raw).replace(":", "-")[:240]


def _memory_row(scope: str, subject: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for item in items if item.get("outcome") == "win")
    sample_size = len(items)
    profits = [_as_float(item.get("profit_units"), 0) for item in items]
    confidences = [_as_float(item.get("confidence"), None) for item in items]
    edges = [_as_float(item.get("value_edge"), None) for item in items]
    avg_conf = _avg([item for item in confidences if item is not None])
    avg_edge = _avg([item for item in edges if item is not None])
    profit_units = round(sum(profits), 2)
    return {
        "memory_id": f"{scope}:{subject}".lower()[:500],
        "scope": scope,
        "subject": subject,
        "source": "betsignal_history",
        "sample_size": sample_size,
        "hit_rate": round((wins / sample_size) * 100, 2) if sample_size else None,
        "roi_units": round((profit_units / sample_size) * 100, 2) if sample_size else None,
        "profit_units": profit_units,
        "avg_confidence": avg_conf,
        "avg_edge": avg_edge,
        "notes": _memory_note(scope, subject, sample_size, wins, profit_units),
        "payload": {
            "wins": wins,
            "losses": sample_size - wins,
            "last_signal_ids": [item.get("signal_id") for item in items[:10]],
        },
        "updated_at": _now(),
    }


def _memory_note(
    scope: str,
    subject: str,
    sample_size: int,
    wins: int,
    profit_units: float,
) -> str:
    hit_rate = round((wins / sample_size) * 100, 1) if sample_size else 0
    return (
        f"{scope} {subject}: {sample_size} sinais fechados, "
        f"{hit_rate}% green, lucro {profit_units}u."
    )


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _as_float(value: Any, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_http_error(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    return f"HTTP {response.status_code}: {response.text[:220]}"
