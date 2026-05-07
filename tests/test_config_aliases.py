from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
