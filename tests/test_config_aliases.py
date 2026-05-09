from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.footballQuantAiSkill.config import load_research_skill_settings
from src.config import load_settings


class ConfigAliasTests(unittest.TestCase):
    def test_load_settings_accepts_api_sports_key_alias(self) -> None:
        with (
            patch("src.config.load_dotenv", return_value=False),
            patch.dict(
                os.environ,
                {
                    "API_SPORTS_KEY": "alias-key",
                    "APP_ENV": "production",
                },
                clear=True,
            ),
        ):
            settings = load_settings()

        self.assertEqual(settings.api_football_key, "alias-key")

    def test_load_settings_accepts_api_football_base_url_alias(self) -> None:
        with (
            patch("src.config.load_dotenv", return_value=False),
            patch.dict(
                os.environ,
                {
                    "API_FOOTBALL_KEY": "primary-key",
                    "API_SPORTS_BASE_URL": "https://example.test",
                },
                clear=True,
            ),
        ):
            settings = load_settings()

        self.assertEqual(settings.api_football_base_url, "https://example.test")

    def test_load_settings_reads_espn_runtime_settings(self) -> None:
        with (
            patch("src.config.load_dotenv", return_value=False),
            patch.dict(
                os.environ,
                {
                    "ESPN_TIMEOUT": "45",
                    "ESPN_MAX_RETRIES": "5",
                    "ESPN_USER_AGENT": "ApexGol-ESPN/2.0",
                    "ESPN_SITE_API_BASE_URL": "https://site.example.test",
                    "ESPN_CORE_API_BASE_URL": "https://core.example.test",
                    "ESPN_WEB_V3_API_BASE_URL": "https://webv3.example.test",
                    "ESPN_CDN_API_BASE_URL": "https://cdn.example.test",
                    "ESPN_NOW_API_BASE_URL": "https://now.example.test",
                },
                clear=True,
            ),
        ):
            settings = load_settings()

        self.assertEqual(settings.espn_timeout, 45)
        self.assertEqual(settings.espn_max_retries, 5)
        self.assertEqual(settings.espn_user_agent, "ApexGol-ESPN/2.0")
        self.assertEqual(settings.espn_site_api_base_url, "https://site.example.test")
        self.assertEqual(settings.espn_core_api_base_url, "https://core.example.test")
        self.assertEqual(settings.espn_web_v3_api_base_url, "https://webv3.example.test")
        self.assertEqual(settings.espn_cdn_api_base_url, "https://cdn.example.test")
        self.assertEqual(settings.espn_now_api_base_url, "https://now.example.test")

    def test_research_skill_settings_accept_supabase_service_key_alias(self) -> None:
        with (
            patch("services.footballQuantAiSkill.config.load_dotenv", return_value=False),
            patch.dict(
                os.environ,
                {
                    "SUPABASE_URL": "https://example.supabase.co",
                    "SUPABASE_SERVICE_KEY": "service-key-alias",
                },
                clear=True,
            ),
        ):
            settings = load_research_skill_settings()

        self.assertEqual(settings.supabase_url, "https://example.supabase.co")
        self.assertEqual(settings.supabase_service_role_key, "service-key-alias")


if __name__ == "__main__":
    unittest.main()
