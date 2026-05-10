from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.footballQuantAiSkill.data_source_service import DataSourceService
from services.footballQuantAiSkill.schemas import SourceRecord
from services.globalAdaptiveIntelligence.data_sources.registry import DataSourceRegistryService


class FootballQuantSourceStatusTests(unittest.TestCase):
    def test_source_status_ignores_malformed_repository_rows(self) -> None:
        service = DataSourceService.__new__(DataSourceService)
        service.repository = SimpleNamespace(
            list_data_sources=lambda: [
                "csv-local",
                {"name": "CSV/JSON Local", "is_active": 1, "priority": 10},
            ]
        )
        source = SimpleNamespace()
        record = SourceRecord(
            name="CSV/JSON Local",
            provider_type="local_file",
            base_url="data/processed",
            api_key_env_name=None,
            priority=10,
            is_active=True,
        )
        service.sources = [(source, record)]

        rows = service.source_status()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "CSV/JSON Local")
        self.assertTrue(rows[0]["is_active"])


class GlobalAIRegistrySeedTests(unittest.TestCase):
    def test_seed_skips_non_dict_source_rows(self) -> None:
        repository = SimpleNamespace(seed_data_sources=Mock())
        service = DataSourceRegistryService.__new__(DataSourceRegistryService)
        service.repository = repository
        service.football_skill = SimpleNamespace(
            data_sources=SimpleNamespace(
                source_status=lambda: [
                    "legacy-string-row",
                    {
                        "name": "API-Football",
                        "provider_type": "api",
                        "base_url": "https://v3.football.api-sports.io",
                        "api_key_env_name": "API_FOOTBALL_KEY",
                        "is_active": True,
                        "priority": 20,
                    },
                ]
            )
        )

        with patch(
            "services.globalAdaptiveIntelligence.data_sources.registry.load_settings",
            return_value=SimpleNamespace(
                espn_site_api_base_url="https://site.api.espn.com",
            ),
        ):
            service.seed()

        rows = repository.seed_data_sources.call_args.args[0]
        names = [row["name"] for row in rows]
        self.assertIn("API-Football", names)
        self.assertIn("ESPN Scoreboard", names)
        self.assertIn("iSports Odds", names)
        self.assertNotIn("legacy-string-row", names)


if __name__ == "__main__":
    unittest.main()
