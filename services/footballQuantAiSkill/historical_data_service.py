from __future__ import annotations

from pathlib import Path
from typing import Any

from .data_source_service import DataSourceService
from .normalization.normalizer import FootballDataNormalizer
from .repository import FootballResearchRepository


class HistoricalDataService:
    def __init__(self, repository: FootballResearchRepository, data_sources: DataSourceService):
        self.repository = repository
        self.data_sources = data_sources
        self.normalizer = FootballDataNormalizer()

    async def import_from_source(self, *, preferred_source: str | None = None, user_id: int | None = None, **kwargs) -> dict[str, Any]:
        source_name, rows = await self.data_sources.get_historical_matches(preferred=preferred_source, **kwargs)
        normalized = [self.normalizer.normalize_match(item, source=source_name) for item in rows]
        result = self.repository.import_normalized_matches(normalized, source_name=source_name, user_id=user_id)
        self.repository.log("historicalDataService", "Importação histórica concluída", payload={"source": source_name, **result}, user_id=user_id)
        return {"source": source_name, **result, "domains_required": self.required_domains()}

    async def import_local_file(self, filename: str, *, user_id: int | None = None) -> dict[str, Any]:
        path = Path(self.data_sources.settings.csv_root) / filename
        source_name, rows = await self.data_sources.get_historical_matches(preferred="CSV/JSON Local", filename=filename)
        normalized = [self.normalizer.normalize_match(item, source=source_name) for item in rows]
        result = self.repository.import_normalized_matches(normalized, source_name=source_name, user_id=user_id)
        self.repository.log("historicalDataService", "Importação local concluída", payload={"path": path.as_posix(), **result}, user_id=user_id)
        return {"source": source_name, "path": path.as_posix(), **result}

    def required_domains(self) -> list[str]:
        return [
            "api-football-v1.p.rapidapi.com",
            "api.football-data.org",
            "api.the-odds-api.com / api.odds-api.io",
            "raw.githubusercontent.com/statsbomb/open-data",
        ]

