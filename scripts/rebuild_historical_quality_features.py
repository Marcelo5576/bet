from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.footballQuantAiSkill.config import load_research_skill_settings
from services.footballQuantAiSkill.feature_engineering.historical_feature_store import HistoricalFeatureStore
from services.footballQuantAiSkill.repository import FootballResearchRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recalcula qualidade, split temporal, features e confiabilidade de ligas.")
    parser.add_argument("--feature-set-version", default="v1_no_leakage")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.time()
    settings = load_research_skill_settings()
    repository = FootballResearchRepository(settings.db_file)
    store = HistoricalFeatureStore(repository)
    result = store.rebuild(
        feature_set_version=args.feature_set_version,
        limit=args.limit or None,
    )
    result["elapsed_seconds"] = round(time.time() - started, 2)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
