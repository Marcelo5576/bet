from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.integrations.supabase import SupabaseSink
from src.portal import DEFAULT_AI_SUPPORT_SKILLS, PortalStore


async def main() -> int:
    settings = load_settings()
    store = PortalStore(settings.portal_db_file)
    store.seed_ai_skills(DEFAULT_AI_SUPPORT_SKILLS)
    skills = store.list_ai_skills()
    print(f"Local AI skills: {len(skills)}")

    sink = SupabaseSink.from_settings(settings)
    if not sink.enabled:
        print("Supabase desativado: configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY.")
        return 0

    await sink.sync_ai_skills(skills)
    remote = await sink.fetch_ai_skills()
    print(f"Supabase AI skills: {len(remote)}")
    if not remote:
        print("Se a contagem ficou 0, aplique a tabela betsignal_ai_skills de supabase_schema.sql no Supabase.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
