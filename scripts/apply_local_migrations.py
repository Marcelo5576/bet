from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.footballQuantAiSkill.repository import FootballResearchRepository
from src.config import load_settings
from src.decision_log import DecisionLogStore
from src.intelligence.football_brain import get_football_brain
from src.portal import PortalStore
from src.storage import StateStore
from src.usage_metrics import UsageTracker


def main() -> None:
    settings = load_settings()
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    portal = PortalStore(settings.portal_db_file)
    admin = portal.ensure_admin(settings.admin_email, settings.admin_name, settings.admin_password)

    state_store = StateStore(settings.state_file)
    state_store.save(state_store.load())

    usage = UsageTracker(settings.usage_metrics_db_file)
    decision = DecisionLogStore(settings.decision_audit_db_file)
    research_path = os.getenv("FOOTBALL_RESEARCH_DB_FILE", "data/football_quant_research.db")
    research = FootballResearchRepository(research_path)
    brain = get_football_brain(settings)

    payload = {
        "ok": True,
        "portal_db": settings.portal_db_file,
        "state_file": settings.state_file,
        "usage_db": settings.usage_metrics_db_file,
        "decision_db": settings.decision_audit_db_file,
        "research_db": research.path.as_posix(),
        "brain_db": settings.brain_db_file if brain else None,
        "admin_email": admin.get("email"),
        "counts": {
            "usage_tables_ready": True if usage else False,
            "decision_tables_ready": True if decision else False,
            "research_tables_ready": True if research else False,
            "brain_enabled": bool(brain),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
