from __future__ import annotations

from typing import Any

from .config import ResearchSkillSettings
from .data_sources.api_football_source import ApiFootballResearchSource
from .data_sources.base import FootballDataSource
from .data_sources.football_data_org_source import FootballDataOrgResearchSource
from .data_sources.local_file_source import LocalFileFootballDataSource
from .data_sources.mock_source import MockFootballDataSource
from .data_sources.statsbomb_open_source import StatsBombOpenResearchSource
from .data_sources.the_odds_api_source import TheOddsApiResearchSource
from .repository import FootballResearchRepository
from .schemas import SourceRecord


class DataSourceService:
    def __init__(self, settings: ResearchSkillSettings, repository: FootballResearchRepository):
        self.settings = settings
        self.repository = repository
        self.sources: list[tuple[FootballDataSource, SourceRecord]] = [
            (
                LocalFileFootballDataSource(settings.csv_root),
                SourceRecord(
                    name="CSV/JSON Local",
                    provider_type="local_file",
                    base_url=settings.csv_root,
                    api_key_env_name=None,
                    priority=10,
                ),
            ),
            (
                MockFootballDataSource(),
                SourceRecord(
                    name="Mock Local",
                    provider_type="mock",
                    base_url="internal://mock",
                    api_key_env_name=None,
                    priority=15,
                    is_active=settings.mock_enabled,
                ),
            ),
            (
                ApiFootballResearchSource(settings.api_football_base_url, settings.api_football_key),
                SourceRecord(
                    name="API-Football",
                    provider_type="api",
                    base_url=settings.api_football_base_url,
                    api_key_env_name="API_FOOTBALL_KEY",
                    priority=20,
                    is_active=bool(settings.api_football_key),
                ),
            ),
            (
                FootballDataOrgResearchSource(settings.football_data_org_base_url, settings.football_data_org_token),
                SourceRecord(
                    name="Football-Data.org",
                    provider_type="api",
                    base_url=settings.football_data_org_base_url,
                    api_key_env_name="FOOTBALL_DATA_ORG_TOKEN",
                    priority=30,
                    is_active=bool(settings.football_data_org_token),
                ),
            ),
            (
                TheOddsApiResearchSource(settings.odds_api_base_url, settings.odds_api_key),
                SourceRecord(
                    name="The Odds API",
                    provider_type="api",
                    base_url=settings.odds_api_base_url,
                    api_key_env_name="ODDS_API_KEY",
                    priority=40,
                    is_active=bool(settings.odds_api_key),
                ),
            ),
            (
                StatsBombOpenResearchSource(settings.statsbomb_open_base_url),
                SourceRecord(
                    name="StatsBomb Open Data",
                    provider_type="open_data",
                    base_url=settings.statsbomb_open_base_url,
                    api_key_env_name=None,
                    priority=50,
                ),
            ),
        ]
        self.repository.seed_data_sources([item[1] for item in self.sources])

    def list_sources(self) -> list[dict[str, Any]]:
        return self.repository.list_data_sources()

    def source_status(self) -> list[dict[str, Any]]:
        rows = [row for row in self.repository.list_data_sources() if isinstance(row, dict)]
        by_name = {
            str(row.get("name") or "").strip(): row
            for row in rows
            if str(row.get("name") or "").strip()
        }
        status_rows: list[dict[str, Any]] = []
        for source, record in self.sources:
            meta = by_name.get(record.name)
            if not isinstance(meta, dict):
                meta = {}
            status_rows.append(
                {
                    "name": record.name,
                    "provider_type": record.provider_type,
                    "base_url": record.base_url,
                    "api_key_env_name": record.api_key_env_name,
                    "is_active": bool(int(meta.get("is_active", 1) if meta else (1 if record.is_active else 0))),
                    "priority": int(meta.get("priority", record.priority) if meta else record.priority),
                    "supports_historical": hasattr(source, "getHistoricalMatches"),
                    "supports_odds": hasattr(source, "getOdds"),
                }
            )
        return status_rows

    async def get_historical_matches(self, *, preferred: str | None = None, **kwargs) -> tuple[str, list[dict[str, Any]]]:
        ordered = self._ordered_sources(preferred)
        errors: list[str] = []
        for source, record in ordered:
            if not record.is_active:
                continue
            try:
                matches = await source.getHistoricalMatches(**kwargs)
            except Exception as exc:
                errors.append(f"{record.name}: {exc}")
                self.repository.log("dataSourceService", f"Falha em {record.name}", level="warning", payload={"error": str(exc), "method": "getHistoricalMatches"})
                continue
            if matches:
                return record.name, matches
        if errors:
            self.repository.log("dataSourceService", "Fallback geral acionado", level="warning", payload={"errors": errors})
        return "Mock Local", await MockFootballDataSource().getHistoricalMatches(**kwargs)

    async def get_fixtures(self, *, preferred: str | None = None, **kwargs) -> tuple[str, list[dict[str, Any]]]:
        ordered = self._ordered_sources(preferred)
        for source, record in ordered:
            if not record.is_active:
                continue
            try:
                fixtures = await source.getFixtures(**kwargs)
            except Exception as exc:
                self.repository.log("dataSourceService", f"Falha em fixtures {record.name}", level="warning", payload={"error": str(exc)})
                continue
            if fixtures:
                return record.name, fixtures
        return "Mock Local", await MockFootballDataSource().getFixtures(**kwargs)

    async def get_odds(self, *, preferred: str | None = None, **kwargs) -> tuple[str, list[dict[str, Any]]]:
        ordered = self._ordered_sources(preferred)
        for source, record in ordered:
            if not record.is_active:
                continue
            try:
                odds = await source.getOdds(**kwargs)
            except Exception as exc:
                self.repository.log("dataSourceService", f"Falha em odds {record.name}", level="warning", payload={"error": str(exc)})
                continue
            if odds:
                return record.name, odds
        return "Mock Local", await MockFootballDataSource().getOdds(**kwargs)

    def _ordered_sources(self, preferred: str | None = None) -> list[tuple[FootballDataSource, SourceRecord]]:
        ordered = sorted(self.sources, key=lambda item: item[1].priority)
        if not preferred:
            return ordered
        preferred_lower = preferred.strip().lower()
        ordered.sort(key=lambda item: 0 if item[1].name.lower() == preferred_lower else 1)
        return ordered
