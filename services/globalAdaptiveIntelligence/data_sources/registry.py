from __future__ import annotations

import os
from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill
from src.config import load_settings

from ..repository import GlobalAdaptiveRepository


class DataSourceRegistryService:
    def __init__(self, repository: GlobalAdaptiveRepository):
        self.repository = repository
        self.football_skill = get_football_quant_ai_skill()

    def seed(self) -> None:
        app_settings = load_settings()
        football_rows = []
        for row in self.football_skill.data_sources.source_status():
            if not isinstance(row, dict):
                continue
            source_name = str(row.get("name") or "").strip()
            if not source_name:
                continue
            football_rows.append(
                {
                    "name": source_name,
                    "domain": _domain_for_source(source_name),
                    "provider_type": str(row.get("provider_type") or "api"),
                    "sport_or_market": "football",
                    "base_url": row.get("base_url"),
                    "api_key_env_name": row.get("api_key_env_name"),
                    "is_active": bool(row.get("is_active")),
                    "priority": int(row.get("priority", 100)),
                    "rate_limit_per_minute": _rate_limit_for_source(source_name),
                    "requires_api_key": bool(row.get("api_key_env_name")),
                    "status": "ready" if row.get("is_active") else "fallback",
                    "notes": "Fonte reaproveitada do footballQuantAiSkill.",
                }
            )
        football_rows.extend(
            [
                {
                    "name": "ESPN Scoreboard",
                    "domain": _domain_for_source("ESPN Scoreboard"),
                    "provider_type": "api",
                    "sport_or_market": "football_live",
                    "base_url": app_settings.espn_site_api_base_url,
                    "api_key_env_name": None,
                    "is_active": True,
                    "priority": 25,
                    "rate_limit_per_minute": 30,
                    "requires_api_key": False,
                    "status": "ready",
                    "notes": "Fallback de placar ao vivo, estatisticas leves e cobertura global de ligas ESPN.",
                },
                {
                    "name": "iSports Odds",
                    "domain": _domain_for_source("iSports Odds"),
                    "provider_type": "api",
                    "sport_or_market": "football_odds",
                    "base_url": (os.getenv("ISPORTS_API_BASE_URL") or "http://api.isportsapi.com").rstrip("/"),
                    "api_key_env_name": "ISPORTS_API_KEY",
                    "is_active": bool((os.getenv("ISPORTS_API_KEY") or "").strip()),
                    "priority": 45,
                    "rate_limit_per_minute": 20,
                    "requires_api_key": True,
                    "status": "ready" if (os.getenv("ISPORTS_API_KEY") or "").strip() else "fallback",
                    "notes": "Odds reais complementares para Asian Handicap, 1X2, Over/Under e linhas HT via companyID 8 (Bet365).",
                },
            ]
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
    if "espn" in key:
        return "site.api.espn.com"
    if "isports" in key:
        return "api.isportsapi.com"
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
    if "isports" in key:
        return 20
    if "gemini" in key:
        return 10
    if "api-football" in key:
        return 30
    if "espn" in key:
        return 30
    return 0
