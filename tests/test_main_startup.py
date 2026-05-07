from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import src.main as app_main


class MainStartupTests(unittest.TestCase):
    @patch("src.main.asyncio.set_event_loop")
    @patch("src.main.asyncio.new_event_loop")
    @patch("src.main.asyncio.get_event_loop", side_effect=RuntimeError("no loop"))
    def test_ensure_polling_event_loop_creates_one_when_missing(
        self,
        get_event_loop_mock,
        new_event_loop_mock,
        set_event_loop_mock,
    ) -> None:
        loop = object()
        new_event_loop_mock.return_value = loop

        result = app_main._ensure_polling_event_loop()

        self.assertIs(result, loop)
        new_event_loop_mock.assert_called_once()
        set_event_loop_mock.assert_called_once_with(loop)

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

    def test_scheduled_scan_updates_dashboard_even_without_telegram_chats(self) -> None:
        async def run_case() -> None:
            settings = SimpleNamespace(
                idle_scan_interval_seconds=20,
                active_scan_interval_seconds=20,
                daily_red_limit=2,
                portal_db_file="data/portal.db",
            )
            state = SimpleNamespace(
                history=[],
                active_game_id=None,
                active_signal=None,
            )
            store = SimpleNamespace(consume_scan_request=Mock(return_value=(state, False)))
            job_queue = SimpleNamespace(run_once=Mock())
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"settings": settings, "store": store}),
                job_queue=job_queue,
            )

            with (
                patch("src.main._notification_chat_ids", return_value=set()),
                patch("src.main._approved_signal_chat_ids", return_value=set()),
                patch("src.main.PortalStore") as portal_store_mock,
                patch("src.main._scanner_cycle_seconds", return_value=20),
                patch("src.main.red_stop_status", return_value={}),
                patch("src.main.refresh_active_signal", new_callable=AsyncMock) as refresh_mock,
                patch("src.main.run_scan", new_callable=AsyncMock, return_value="scan-ok") as run_scan_mock,
            ):
                portal_store_mock.return_value.notification_scan_preferences.return_value = (20, 20)
                await app_main.scheduled_scan(context)

            job_queue.run_once.assert_called_once()
            refresh_mock.assert_not_awaited()
            run_scan_mock.assert_awaited_once()

        import asyncio

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
