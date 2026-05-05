from __future__ import annotations

from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill

from ..repository import GlobalAdaptiveRepository


class DataSourceRegistryService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository
        self.football_skill = get_football_quant_ai_skill()

    def seed(self) -> None:
        football_rows = []
        for row in self.football_skill.data_sources.source_status():
            football_rows.append(
                {
                    "name": row["name"],
                    "domain": _domain_for_source(row["name"]),
                    "provider_type": row["provider_type"],
                    "sport_or_market": "football",
                    "base_url": row["base_url"],
                    "api_key_env_name": row.get("api_key_env_name"),
                    "is_active": bool(row.get("is_active")),
                    "priority": int(row.get("priority", 100)),
                    "rate_limit_per_minute": _rate_limit_for_source(row["name"]),
                    "requires_api_key": bool(row.get("api_key_env_name")),
                    "status": "ready" if row.get("is_active") else "fallback",
                    "notes": "Fonte reaproveitada do footballQuantAiSkill.",
                }
            )
        future_rows = [
            {
                "name": "CoinGecko",
                "domain": "coingecko.com",
                "provider_type": "api",
                "sport_or_market": "crypto",
                "base_url": "https://api.coingecko.com/api/v3",
                "api_key_env_name": None,
                "is_active": False,
                "priority": 200,
                "rate_limit_per_minute": 30,
                "requires_api_key": False,
                "status": "planned",
                "notes": "Preparado para futuro módulo crypto.",
            },
            {
                "name": "Yahoo Finance",
                "domain": "finance.yahoo.com",
                "provider_type": "api",
                "sport_or_market": "financial",
                "base_url": "https://query1.finance.yahoo.com",
                "api_key_env_name": None,
                "is_active": False,
                "priority": 210,
                "rate_limit_per_minute": 30,
                "requires_api_key": False,
                "status": "planned",
                "notes": "Preparado para futuro módulo financeiro.",
            },
        ]
        self.repository.seed_data_sources(football_rows + future_rows)

    def list_sources(self) -> list[dict[str, Any]]:
        return self.repository.list_data_sources()


def _domain_for_source(name: str) -> str:
    key = (name or "").strip().lower()
    if "api-football" in key:
        return "api-football-v1.p.rapidapi.com"
    if "football-data" in key:
        return "api.football-data.org"
    if "odds" in key:
        return "api.odds-api.io"
    if "statsbomb" in key:
        return "raw.githubusercontent.com/statsbomb/open-data"
    if "csv" in key:
        return "local"
    return "internal"


def _rate_limit_for_source(name: str) -> int:
    key = (name or "").strip().lower()
    if "odds" in key:
        return 20
    if "gemini" in key:
        return 10
    if "api-football" in key:
        return 30
    return 0

