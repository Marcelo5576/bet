from __future__ import annotations

from types import SimpleNamespace
import unittest

from services.footballQuantAiSkill import FootballQuantAiSkill


class FootballQuantHealthTests(unittest.TestCase):
    def test_health_survives_bad_source_status_and_invalid_supabase_status(self) -> None:
        skill = FootballQuantAiSkill.__new__(FootballQuantAiSkill)
        skill.settings = SimpleNamespace(
            db_file="data/football_quant_research.db",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-key",
        )
        skill.repository = SimpleNamespace(
            system_snapshot=lambda: {
                "db_file": "data/football_quant_research.db",
                "counts": {"historical_matches": 12, "historical_features": 8},
            }
        )
        skill.data_sources = SimpleNamespace(source_status=lambda: (_ for _ in ()).throw(AttributeError("'str' object has no attribute 'get'")))
        skill.supabase = SimpleNamespace(
            sync_status=lambda: "broken",
            hydrate_local_cache_if_needed=lambda **kwargs: {"enabled": True, "skipped": True},
        )

        payload = skill.health()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"]["historical_matches"], 12)
        self.assertEqual(payload["sources"], [])
        self.assertEqual(payload["supabase"]["schema_mode"], "unavailable")
        self.assertIn("status_invalido", payload["supabase"]["last_error"])


if __name__ == "__main__":
    unittest.main()
