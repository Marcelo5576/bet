from __future__ import annotations

from typing import Any

from ..repository import FootballResearchRepository


class ResearchSupabaseSyncService:
    def __init__(self, repository: FootballResearchRepository, *, supabase_url: str | None, supabase_service_role_key: str | None):
        self.repository = repository
        self.supabase_url = supabase_url
        self.supabase_service_role_key = supabase_service_role_key

    @property
    def enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    def sync_status(self) -> dict[str, Any]:
        snapshot = self.repository.system_snapshot()
        return {
            "enabled": self.enabled,
            "supabase_url": self.supabase_url or "",
            "local_snapshot": snapshot,
            "note": "As tabelas SQL ficam na migration reversível. A execução remota depende da sua credencial Supabase.",
        }

