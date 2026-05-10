from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

import httpx

from ..repository import FootballResearchRepository
from ..schemas import NormalizedMatch

logger = logging.getLogger("football_quant.supabase")

_HISTORICAL_TABLES = (
    "historical_matches",
    "historical_features",
    "league_reliability_scores",
)
_LEGACY_TABLES = (
    "betsignal_games",
    "betsignal_signals",
    "betsignal_ai_memory",
    "betsignal_ai_skills",
)
_LEGACY_SOURCE_NAME = "Supabase Legacy"


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _to_datetime(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchSupabaseSyncService:
    def __init__(self, repository: FootballResearchRepository, *, supabase_url: str | None, supabase_service_role_key: str | None):
        self.repository = repository
        self.supabase_url = (supabase_url or "").rstrip("/")
        self.supabase_service_role_key = (supabase_service_role_key or "").strip()
        self.last_hydrate_at: datetime | None = None
        self.last_hydrate_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.last_capability_check_at: datetime | None = None
        self._remote_capabilities: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def sync_status(self) -> dict[str, Any]:
        snapshot = self.repository.system_snapshot()
        capabilities = self._remote_capabilities_snapshot()
        return {
            "enabled": self.enabled,
            "supabase_url": self.supabase_url or "",
            "local_snapshot": snapshot,
            "last_hydrate_at": self.last_hydrate_at.isoformat() if self.last_hydrate_at else None,
            "last_hydrate_result": self.last_hydrate_result or {},
            "last_error": self.last_error,
            "schema_mode": capabilities["schema_mode"],
            "available_tables": capabilities["available_tables"],
            "table_probe_errors": capabilities["table_probe_errors"],
            "last_capability_check_at": self.last_capability_check_at.isoformat() if self.last_capability_check_at else None,
            "note": "Quando habilitado, a camada de pesquisa usa o Supabase para refrescar histórico e features no cache local. Se o projeto remoto ainda estiver no esquema legado betsignal_*, fazemos hidratação compatível sem quebrar o cache local.",
        }

    def hydrate_local_cache_if_needed(
        self,
        *,
        max_age_minutes: int = 30,
        min_local_matches: int = 800,
        min_local_features: int = 800,
        recent_match_limit: int = 300,
        recent_feature_limit: int = 600,
    ) -> dict[str, Any]:
        snapshot = self.repository.system_snapshot()
        counts = snapshot["counts"]
        if not self.enabled:
            return {
                "enabled": False,
                "skipped": True,
                "reason": "supabase_disabled",
                "local_matches": counts.get("historical_matches", 0),
                "local_features": counts.get("historical_features", 0),
            }
        if self.last_hydrate_at and datetime.now(timezone.utc) - self.last_hydrate_at < timedelta(minutes=max_age_minutes):
            return {
                "enabled": True,
                "skipped": True,
                "reason": "fresh_cache",
                "last_hydrate_at": self.last_hydrate_at.isoformat(),
                "last_hydrate_result": self.last_hydrate_result or {},
            }

        needs_fuller_load = (
            counts.get("historical_matches", 0) < min_local_matches
            or counts.get("historical_features", 0) < min_local_features
        )
        return self.hydrate_local_cache(
            match_limit=max(recent_match_limit, min_local_matches if needs_fuller_load else recent_match_limit),
            feature_limit=max(recent_feature_limit, min_local_features if needs_fuller_load else recent_feature_limit),
        )

    def hydrate_local_cache(self, *, match_limit: int = 300, feature_limit: int = 600) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "skipped": True, "reason": "supabase_disabled"}

        try:
            capabilities = self._remote_capabilities_snapshot(force=True)
            schema_mode = capabilities["schema_mode"]
            available_tables = capabilities["available_tables"]

            if schema_mode == "historical" and available_tables.get("historical_matches"):
                try:
                    payload = self._hydrate_from_historical_tables(
                        match_limit=max(50, int(match_limit)),
                        feature_limit=max(50, int(feature_limit)),
                        available_tables=available_tables,
                    )
                except Exception as exc:
                    if available_tables.get("betsignal_games"):
                        logger.warning(
                            "Schema historico falhou (%s); tentando fallback legacy_betsignal.",
                            exc,
                        )
                        payload = self._hydrate_from_legacy_betsignal(
                            match_limit=max(50, int(match_limit)),
                            feature_limit=max(50, int(feature_limit)),
                            available_tables=available_tables,
                        )
                    else:
                        raise
            elif schema_mode == "legacy_betsignal" and available_tables.get("betsignal_games"):
                payload = self._hydrate_from_legacy_betsignal(
                    match_limit=max(50, int(match_limit)),
                    feature_limit=max(50, int(feature_limit)),
                    available_tables=available_tables,
                )
            else:
                self.last_hydrate_at = datetime.now(timezone.utc)
                self.last_error = None
                self.last_hydrate_result = {
                    "schema_mode": schema_mode,
                    "available_tables": available_tables,
                    "imported_matches": 0,
                    "imported_features": 0,
                    "imported_league_scores": 0,
                    "reason": "remote_schema_unavailable",
                }
                logger.info("Supabase research hydration pulada: nenhum schema remoto compativel encontrado.")
                return {"enabled": True, "skipped": True, **self.last_hydrate_result}

            self.last_hydrate_at = datetime.now(timezone.utc)
            self.last_error = None
            self.last_hydrate_result = payload
            logger.info("Supabase research hydration concluida: %s", payload)
            return {"enabled": True, "skipped": False, **payload}
        except Exception as exc:
            self.last_hydrate_at = datetime.now(timezone.utc)
            self.last_error = str(exc)
            self.last_hydrate_result = {
                "imported_matches": 0,
                "imported_features": 0,
                "imported_league_scores": 0,
                "reason": "hydrate_failed",
            }
            logger.warning("Falha ao hidratar cache de pesquisa a partir do Supabase: %s", exc)
            return {"enabled": True, "skipped": True, "reason": "hydrate_failed", "error": str(exc)}

    def _hydrate_from_historical_tables(
        self,
        *,
        match_limit: int,
        feature_limit: int,
        available_tables: dict[str, bool],
    ) -> dict[str, Any]:
        matches = self._fetch_rows(
            "historical_matches",
            select=(
                "id,external_id,external_fixture_id,source,source_provider,league,league_name,country,"
                "season,match_date,home_team,away_team,status,home_score,away_score,minute,"
                "normalized_payload,raw_payload,data_quality_score,usable_for_training,temporal_split"
            ),
            order="match_date.desc",
            limit=match_limit,
        )
        normalized = [self._to_normalized_match(row) for row in matches]
        imported_matches = self.repository.import_normalized_matches(
            normalized,
            source_name="Supabase Historical",
        )["imported_matches"]

        features: list[dict[str, Any]] = []
        imported_features = 0
        if available_tables.get("historical_features"):
            features = self._fetch_rows(
                "historical_features",
                select=(
                    "match_id,feature_set_version,temporal_split,home_recent_form_5,away_recent_form_5,"
                    "home_goals_avg_5,away_goals_avg_5,home_conceded_avg_5,away_conceded_avg_5,"
                    "home_xg_avg_5,away_xg_avg_5,home_strength,away_strength,"
                    "market_implied_probability,closing_line_value,data_quality_score,"
                    "usable_for_training,context_match_count,created_at"
                ),
                order="created_at.desc",
                limit=feature_limit,
            )
            imported_features = self.repository.upsert_historical_features(features)

        reliability: list[dict[str, Any]] = []
        imported_leagues = 0
        if available_tables.get("league_reliability_scores"):
            reliability = self._fetch_rows(
                "league_reliability_scores",
                select=(
                    "league,season,match_count,trainable_count,odds_count,stats_count,avg_data_quality,"
                    "roi_simulated,drawdown,stability_score,league_reliability_score,classification,"
                    "reasons_json,calculated_at"
                ),
                order="calculated_at.desc",
                limit=120,
            )
            imported_leagues = self.repository.upsert_league_reliability_scores(reliability)

        return {
            "schema_mode": "historical",
            "available_tables": available_tables,
            "imported_matches": imported_matches,
            "imported_features": imported_features,
            "imported_league_scores": imported_leagues,
            "fetched_matches": len(matches),
            "fetched_features": len(features),
            "fetched_league_scores": len(reliability),
        }

    def _hydrate_from_legacy_betsignal(
        self,
        *,
        match_limit: int,
        feature_limit: int,
        available_tables: dict[str, bool],
    ) -> dict[str, Any]:
        games = self._fetch_rows(
            "betsignal_games",
            select=(
                "game_id,league,division,home,away,minute,home_goals,away_goals,home_pressure,"
                "away_pressure,home_shots_on,away_shots_on,odds_home,odds_draw,odds_away,"
                "priority,markets,raw,updated_at"
            ),
            order="updated_at.desc",
            limit=match_limit,
        )
        normalized = [self._legacy_game_to_normalized_match(row) for row in games if str(row.get("game_id") or "").strip()]
        imported_matches = self.repository.import_normalized_matches(
            normalized,
            source_name=_LEGACY_SOURCE_NAME,
        )["imported_matches"]

        game_ids = [str(row.get("game_id") or "").strip() for row in games if str(row.get("game_id") or "").strip()]
        local_match_ids = self._local_match_ids_for_external_ids(game_ids, source=_LEGACY_SOURCE_NAME)

        signals: list[dict[str, Any]] = []
        if available_tables.get("betsignal_signals"):
            quoted_ids = ",".join(f'"{value}"' for value in game_ids[: min(len(game_ids), 300)])
            params = {}
            if quoted_ids:
                params["game_id"] = f"in.({quoted_ids})"
            signals = self._fetch_rows(
                "betsignal_signals",
                select=(
                    "signal_id,game_id,market,entry_market,confidence,target_odds,entry_odds,"
                    "data_quality,value_edge,profit_units,outcome,payload,created_at,updated_at"
                ),
                order="created_at.desc",
                limit=max(feature_limit, 200),
                extra_params=params,
            )

        features = self._build_legacy_features(games, signals, local_match_ids, feature_limit=feature_limit)
        imported_features = self.repository.upsert_historical_features(features)

        league_scores = self._build_legacy_league_scores(games, signals)
        imported_leagues = self.repository.upsert_league_reliability_scores(league_scores)

        return {
            "schema_mode": "legacy_betsignal",
            "available_tables": available_tables,
            "imported_matches": imported_matches,
            "imported_features": imported_features,
            "imported_league_scores": imported_leagues,
            "fetched_matches": len(games),
            "fetched_features": len(features),
            "fetched_league_scores": len(league_scores),
            "fetched_signals": len(signals),
        }

    def _fetch_rows(self, table: str, *, select: str, order: str, limit: int, extra_params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        params = {"select": select, "order": order, "limit": str(limit)}
        if extra_params:
            params.update(extra_params)
        with httpx.Client(timeout=25.0) as client:
            response = client.get(
                f"{self.supabase_url}/rest/v1/{table}",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else []

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.supabase_service_role_key,
            "Authorization": f"Bearer {self.supabase_service_role_key}",
        }

    def _remote_capabilities_snapshot(self, *, force: bool = False, max_age_minutes: int = 30) -> dict[str, Any]:
        if not self.enabled:
            return {
                "schema_mode": "disabled",
                "available_tables": {},
                "table_probe_errors": {},
            }
        if (
            not force
            and self._remote_capabilities is not None
            and self.last_capability_check_at is not None
            and datetime.now(timezone.utc) - self.last_capability_check_at < timedelta(minutes=max_age_minutes)
        ):
            return self._remote_capabilities

        table_probe_errors: dict[str, str] = {}
        available_tables: dict[str, bool] = {}
        for table in (*_HISTORICAL_TABLES, *_LEGACY_TABLES):
            available, error = self._probe_table(table)
            available_tables[table] = available
            if error:
                table_probe_errors[table] = error

        if available_tables.get("historical_matches"):
            schema_mode = "historical"
        elif available_tables.get("betsignal_games"):
            schema_mode = "legacy_betsignal"
        else:
            schema_mode = "local_only"

        self.last_capability_check_at = datetime.now(timezone.utc)
        self._remote_capabilities = {
            "schema_mode": schema_mode,
            "available_tables": available_tables,
            "table_probe_errors": table_probe_errors,
        }
        return self._remote_capabilities

    def _probe_table(self, table: str) -> tuple[bool, str | None]:
        probe_select = {
            "historical_matches": "id",
            "historical_features": "id",
            "league_reliability_scores": "id",
            "betsignal_games": "game_id",
            "betsignal_signals": "signal_id",
            "betsignal_ai_memory": "memory_id",
            "betsignal_ai_skills": "skill_id",
        }.get(table, "id")
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(
                    f"{self.supabase_url}/rest/v1/{table}",
                    headers=self._headers(),
                    params={"select": probe_select, "limit": "1"},
                )
            if response.status_code in {200, 206}:
                return True, None
            if response.status_code == 404:
                return False, response.text[:240]
            response.raise_for_status()
            return True, None
        except httpx.HTTPStatusError as exc:
            return False, exc.response.text[:240]
        except Exception as exc:
            return False, str(exc)

    def _legacy_game_to_normalized_match(self, row: dict[str, Any]) -> NormalizedMatch:
        raw_payload = _loads(row.get("raw"), {})
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        markets = _loads(row.get("markets"), {})
        if not isinstance(markets, dict):
            markets = {}
        updated_at = _to_datetime(row.get("updated_at"))
        minute = _as_int(row.get("minute"))
        stats = {
            "shots_on_home": _as_int(row.get("home_shots_on")),
            "shots_on_away": _as_int(row.get("away_shots_on")),
            "dangerous_attacks_home": _as_int(row.get("home_pressure")),
            "dangerous_attacks_away": _as_int(row.get("away_pressure")),
            "attacks_home": _as_int(row.get("home_pressure")),
            "attacks_away": _as_int(row.get("away_pressure")),
        }
        stats = {key: value for key, value in stats.items() if value is not None}
        odds: list[dict[str, Any]] = []
        if any(row.get(key) not in (None, "") for key in ("odds_home", "odds_draw", "odds_away")):
            odds.append(
                {
                    "timestamp": updated_at.isoformat(),
                    "market": "1x2",
                    "home_odd": _as_float(row.get("odds_home")),
                    "draw_odd": _as_float(row.get("odds_draw")),
                    "away_odd": _as_float(row.get("odds_away")),
                    "bookmaker": "legacy_supabase",
                    "source": _LEGACY_SOURCE_NAME,
                }
            )
        payload = {
            **raw_payload,
            "game_id": row.get("game_id"),
            "league": {
                "name": row.get("league"),
                "country": "Brasil" if "brasil" in str(row.get("division") or "").strip().lower() else "",
            },
            "league_name": row.get("league"),
            "division": row.get("division"),
            "home_team": row.get("home"),
            "away_team": row.get("away"),
            "match_date": updated_at.isoformat(),
            "minute": minute,
            "home_goals": _as_int(row.get("home_goals")),
            "away_goals": _as_int(row.get("away_goals")),
            "markets": markets,
            "stats": stats,
            "odds": odds,
            "source": _LEGACY_SOURCE_NAME,
        }
        return NormalizedMatch(
            external_id=str(row.get("game_id") or ""),
            league=str(row.get("league") or row.get("division") or "Legacy BetSignal"),
            country="Brasil" if "brasil" in str(row.get("division") or "").strip().lower() else "",
            season=updated_at.year,
            match_date=updated_at,
            home_team=str(row.get("home") or ""),
            away_team=str(row.get("away") or ""),
            status=self._legacy_status_from_row(row),
            home_goals=_as_int(row.get("home_goals")),
            away_goals=_as_int(row.get("away_goals")),
            minute=minute,
            source=_LEGACY_SOURCE_NAME,
            stats=stats,
            odds=odds,
            raw_payload=payload,
        )

    def _legacy_status_from_row(self, row: dict[str, Any]) -> str:
        minute = _as_int(row.get("minute"))
        if minute is None or minute <= 0:
            return "NS"
        if minute >= 90:
            return "FT"
        return "LIVE"

    def _local_match_ids_for_external_ids(self, external_ids: list[str], *, source: str) -> dict[str, int]:
        cleaned = [item for item in external_ids if item]
        if not cleaned:
            return {}
        placeholders = ",".join("?" for _ in cleaned)
        query = (
            "SELECT id, external_id FROM historical_matches "
            f"WHERE source = ? AND external_id IN ({placeholders})"
        )
        with self.repository.connect() as conn:
            rows = conn.execute(query, [source, *cleaned]).fetchall()
        return {str(row["external_id"]): int(row["id"]) for row in rows}

    def _build_legacy_features(
        self,
        games: list[dict[str, Any]],
        signals: list[dict[str, Any]],
        local_match_ids: dict[str, int],
        *,
        feature_limit: int,
    ) -> list[dict[str, Any]]:
        signals_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in signals:
            game_id = str(row.get("game_id") or "").strip()
            if game_id:
                signals_by_game[game_id].append(row)

        features: list[dict[str, Any]] = []
        for game in games:
            game_id = str(game.get("game_id") or "").strip()
            match_id = local_match_ids.get(game_id)
            if not match_id:
                continue
            related = signals_by_game.get(game_id, [])
            confidences = [_as_float(item.get("confidence")) for item in related]
            value_edges = [_as_float(item.get("value_edge")) for item in related]
            data_quality_values = [_as_float(item.get("data_quality")) for item in related]
            valid_odds = [value for value in (_as_float(game.get("odds_home")), _as_float(game.get("odds_draw")), _as_float(game.get("odds_away"))) if value and value > 1.0]
            implied_probability = round(1 / min(valid_odds), 4) if valid_odds else None
            closed_signals = [item for item in related if str(item.get("outcome") or "").strip().lower() not in {"", "open", "pending"}]
            base_quality = self._legacy_game_quality(game)
            if data_quality_values:
                base_quality = round((base_quality + _average(data_quality_values)) / 2, 2)
            features.append(
                {
                    "match_id": match_id,
                    "feature_set_version": "supabase_legacy",
                    "temporal_split": "historical_remote_legacy",
                    "home_recent_form_5": None,
                    "away_recent_form_5": None,
                    "home_goals_avg_5": None,
                    "away_goals_avg_5": None,
                    "home_conceded_avg_5": None,
                    "away_conceded_avg_5": None,
                    "home_xg_avg_5": None,
                    "away_xg_avg_5": None,
                    "home_strength": _scale_metric(game.get("home_pressure")),
                    "away_strength": _scale_metric(game.get("away_pressure")),
                    "market_implied_probability": implied_probability,
                    "closing_line_value": round(_average(value_edges), 4) if value_edges else None,
                    "data_quality_score": int(round(base_quality)),
                    "usable_for_training": bool(closed_signals or self._legacy_status_from_row(game) == "FT"),
                    "context_match_count": len(related),
                    "created_at": str(game.get("updated_at") or _now_iso()),
                }
            )
            if len(features) >= feature_limit:
                break
        return features

    def _build_legacy_league_scores(self, games: list[dict[str, Any]], signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        signals_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in signals:
            game_id = str(row.get("game_id") or "").strip()
            if game_id:
                signals_by_game[game_id].append(row)

        games_by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for game in games:
            league = str(game.get("league") or game.get("division") or "").strip()
            if league:
                games_by_league[league].append(game)

        rows: list[dict[str, Any]] = []
        for league, league_games in games_by_league.items():
            league_signals: list[dict[str, Any]] = []
            for game in league_games:
                league_signals.extend(signals_by_game.get(str(game.get("game_id") or "").strip(), []))
            match_count = len(league_games)
            odds_count = sum(1 for game in league_games if any(game.get(key) not in (None, "") for key in ("odds_home", "odds_draw", "odds_away")))
            stats_count = sum(1 for game in league_games if any(game.get(key) not in (None, "") for key in ("home_pressure", "away_pressure", "home_shots_on", "away_shots_on")))
            avg_data_quality = round(_average([self._legacy_game_quality(game) for game in league_games]), 2)
            closed_signals = [item for item in league_signals if str(item.get("outcome") or "").strip().lower() not in {"", "open", "pending"}]
            profits = [_as_float(item.get("profit_units"), 0.0) for item in closed_signals]
            roi_simulated = round((sum(profits) / len(profits)) * 100, 2) if profits else 0.0
            drawdown = round(_max_drawdown(profits), 2) if profits else 0.0
            odds_ratio = odds_count / match_count if match_count else 0.0
            stats_ratio = stats_count / match_count if match_count else 0.0
            stability_score = round(
                min(
                    100.0,
                    30.0
                    + min(match_count, 80) * 0.45
                    + odds_ratio * 20.0
                    + stats_ratio * 20.0
                    + min(len(closed_signals), 25) * 0.4,
                ),
                2,
            )
            league_reliability_score = round(
                min(
                    100.0,
                    max(
                        0.0,
                        avg_data_quality * 0.45
                        + odds_ratio * 20.0
                        + stats_ratio * 15.0
                        + min(len(closed_signals), 30) * 0.5
                        + max(min(roi_simulated, 20.0), -20.0) * 0.35
                        - min(drawdown, 40.0) * 0.25,
                    ),
                ),
                2,
            )
            classification = _league_classification(league_reliability_score)
            reasons = [
                f"Derivado das tabelas legadas betsignal_* ({match_count} jogos remotos).",
                f"Cobertura de odds em {odds_count}/{match_count} jogos.",
                f"Estatisticas ao vivo em {stats_count}/{match_count} jogos.",
            ]
            if closed_signals:
                reasons.append(f"{len(closed_signals)} sinais fechados usados para ROI e drawdown.")
            else:
                reasons.append("Sem sinais fechados suficientes; score priorizou qualidade e cobertura de dados.")
            rows.append(
                {
                    "league": league,
                    "season": max((_to_datetime(game.get("updated_at")).year for game in league_games), default=datetime.now(timezone.utc).year),
                    "match_count": match_count,
                    "trainable_count": sum(1 for game in league_games if self._legacy_status_from_row(game) == "FT"),
                    "odds_count": odds_count,
                    "stats_count": stats_count,
                    "avg_data_quality": avg_data_quality,
                    "roi_simulated": roi_simulated,
                    "drawdown": drawdown,
                    "stability_score": stability_score,
                    "league_reliability_score": league_reliability_score,
                    "classification": classification,
                    "reasons": reasons,
                    "calculated_at": _now_iso(),
                }
            )
        return rows

    def _to_normalized_match(self, row: dict[str, Any]) -> NormalizedMatch:
        payload = _loads(row.get("normalized_payload"), {})
        stats = payload.get("stats") if isinstance(payload, dict) else {}
        odds = payload.get("odds") if isinstance(payload, dict) else []
        raw_payload = _loads(row.get("raw_payload"), {})
        return NormalizedMatch(
            external_id=str(row.get("external_id") or row.get("external_fixture_id") or f"supabase-{row.get('id')}"),
            league=str(row.get("league_name") or row.get("league") or "Unknown League"),
            country=str(row.get("country") or (payload.get("country") if isinstance(payload, dict) else "") or ""),
            season=int(row.get("season") or (payload.get("season") if isinstance(payload, dict) else 0) or 0),
            match_date=_to_datetime(row.get("match_date") or (payload.get("match_date") if isinstance(payload, dict) else None)),
            home_team=str(row.get("home_team") or (payload.get("home_team") if isinstance(payload, dict) else "") or ""),
            away_team=str(row.get("away_team") or (payload.get("away_team") if isinstance(payload, dict) else "") or ""),
            status=str(row.get("status") or (payload.get("status") if isinstance(payload, dict) else "FT") or "FT"),
            home_goals=row.get("home_score", payload.get("home_goals") if isinstance(payload, dict) else None),
            away_goals=row.get("away_score", payload.get("away_goals") if isinstance(payload, dict) else None),
            minute=row.get("minute", payload.get("minute") if isinstance(payload, dict) else None),
            source=str(row.get("source_provider") or row.get("source") or "Supabase Historical"),
            stats=stats if isinstance(stats, dict) else {},
            odds=odds if isinstance(odds, list) else [],
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
        )

    def _legacy_game_quality(self, row: dict[str, Any]) -> float:
        score = 25.0
        if any(row.get(key) not in (None, "") for key in ("home_goals", "away_goals", "minute")):
            score += 20.0
        if any(row.get(key) not in (None, "") for key in ("home_pressure", "away_pressure", "home_shots_on", "away_shots_on")):
            score += 25.0
        if any(row.get(key) not in (None, "") for key in ("odds_home", "odds_draw", "odds_away")):
            score += 20.0
        if _loads(row.get("raw"), {}):
            score += 10.0
        return min(100.0, score)


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _average(values: list[float | None]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return 0.0
    return sum(cleaned) / len(cleaned)


def _scale_metric(value: Any) -> float | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    return round(max(0.0, min(1.0, numeric / 100.0)), 4)


def _max_drawdown(profits: list[float]) -> float:
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for profit in profits:
        running += float(profit or 0.0)
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    return max_drawdown


def _league_classification(score: float) -> str:
    if score >= 75:
        return "Boa para operar"
    if score >= 55:
        return "Em observação"
    return "Evitar"
