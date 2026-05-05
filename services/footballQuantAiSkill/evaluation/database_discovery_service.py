from __future__ import annotations

from pathlib import Path
from typing import Any

from src.integrations.supabase import SupabaseSink

from ..repository import FootballResearchRepository


class DatabaseDiscoveryService:
    def __init__(self, repository: FootballResearchRepository, *, portal_db_file: str, state_file: str, brain_db_file: str, supabase_url: str | None, supabase_service_role_key: str | None):
        self.repository = repository
        self.portal_db_file = portal_db_file
        self.state_file = state_file
        self.brain_db_file = brain_db_file
        self.supabase_url = supabase_url
        self.supabase_service_role_key = supabase_service_role_key

    def scan(self) -> dict[str, Any]:
        return {
            "research_db": self.repository.discovery_report(),
            "existing_local_assets": {
                "portal_db_exists": Path(self.portal_db_file).exists(),
                "state_file_exists": Path(self.state_file).exists(),
                "brain_db_exists": Path(self.brain_db_file).exists(),
            },
            "supabase": {
                "configured": bool(self.supabase_url and self.supabase_service_role_key),
                "url": self.supabase_url or "",
            },
        }

