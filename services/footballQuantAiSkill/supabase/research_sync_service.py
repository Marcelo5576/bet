from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any

import httpx

from ..repository import FootballResearchRepository
from ..schemas import NormalizedMatch

logger = logging.getLogger("football_quant.supabase")


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


class ResearchSupabaseSyncService:
    def __init__(self, repository: FootballResearchRepository, *, supabase_url: str | None, supabase_service_role_key: str | None):
        self.repository = repository
        self.supabase_url = (supabase_url or "").rstrip("/")
        self.supabase_service_role_key = (supabase_service_role_key or "").strip()
        self.last_hydrate_at: datetime | None = None
        self.last_hydrate_result: dict[str, Any] | None = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def sync_status(self) -> dict[str, Any]:
        snapshot = self.repository.system_snapshot()
        return {
            "enabled": self.enabled,
            "supabase_url": self.supabase_url or "",
            "local_snapshot": snapshot,
            "last_hydrate_at": self.last_hydrate_at.isoformat() if self.last_hydrate_at else None,
            "last_hydrate_result": self.last_hydrate_result or {},
            "last_error": self.last_error,
            "note": "Quando habilitado, a camada de pesquisa usa o Supabase para refrescar histórico e features no cache local.",
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
            matches = self._fetch_rows(
                "historical_matches",
                select=(
                    "id,external_id,external_fixture_id,source,source_provider,league,league_name,country,"
                    "season,match_date,home_team,away_team,status,home_score,away_score,minute,"
                    "normalized_payload,raw_payload,data_quality_score,usable_for_training,temporal_split"
                ),
                order="match_date.desc",
                limit=max(50, int(match_limit)),
            )
            normalized = [self._to_normalized_match(row) for row in matches]
            imported_matches = self.repository.import_normalized_matches(
                normalized,
                source_name="Supabase Historical",
            )["imported_matches"]

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
                limit=max(50, int(feature_limit)),
            )
            imported_features = self.repository.upsert_historical_features(features)

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

            self.last_hydrate_at = datetime.now(timezone.utc)
            self.last_error = None
            self.last_hydrate_result = {
                "imported_matches": imported_matches,
                "imported_features": imported_features,
                "imported_league_scores": imported_leagues,
                "fetched_matches": len(matches),
                "fetched_features": len(features),
                "fetched_league_scores": len(reliability),
            }
            logger.info(
                "Supabase research hydration concluida: %s",
                self.last_hydrate_result,
            )
            return {"enabled": True, "skipped": False, **self.last_hydrate_result}
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

    def _fetch_rows(self, table: str, *, select: str, order: str, limit: int) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        params = {"select": select, "order": order, "limit": str(limit)}
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
