from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import src.main as app_main


class MainStartupTests(unittest.TestCase):
    @patch("src.main.asyncio.run")
    @patch("src.main._passive_service_loop", return_value="passive-loop")
    @patch("src.main.load_settings")
    def test_main_without_telegram_token_enters_passive_mode(
        self,
        load_settings_mock,
        passive_loop_mock,
        asyncio_run_mock,
    ) -> None:
        load_settings_mock.return_value = SimpleNamespace(telegram_bot_token="")

        app_main.main()

        passive_loop_mock.assert_called_once()
        asyncio_run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
