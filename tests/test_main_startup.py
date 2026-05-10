from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    @patch("src.main._passive_service_loop", new_callable=Mock, return_value="passive-loop")
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

    def test_approved_signals_are_marked_only_after_successful_dispatch(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
        signal = {
            "signal_id": "sig-1",
            "game": {"game_id": "g-1"},
            "entry_market": "Goals",
            "entry_selection": "Over 1.5",
            "entry_line": "1.5",
        }
        state = SimpleNamespace(approved_signal_alerts={}, active_signal=signal, candidate_signals=[], history=[])

        with patch("src.main._is_approved_signal_for_telegram", return_value=True):
            pending = app_main._approved_signals_to_alert(state, now=now)

        self.assertEqual(pending, [signal])
        self.assertEqual(state.approved_signal_alerts, {})

        store_state = SimpleNamespace(approved_signal_alerts={}, active_signal=None, candidate_signals=[], history=[])
        store = SimpleNamespace(load=Mock(return_value=store_state), save=Mock())
        key = app_main._approved_signal_alert_key(signal)

        app_main._mark_approved_signals_alerted(store, {key}, now=now)

        self.assertEqual(store_state.approved_signal_alerts, {key: now.isoformat()})
        store.save.assert_called_once()

    def test_approved_signal_alerts_are_pruned_before_marking_new_success(self) -> None:
        now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
        fresh_time = (now - timedelta(minutes=5)).isoformat()
        stale_time = (now - timedelta(minutes=25)).isoformat()
        state = SimpleNamespace(
            approved_signal_alerts={"fresh": fresh_time, "stale": stale_time},
            active_signal=None,
            candidate_signals=[],
            history=[],
        )
        store = SimpleNamespace(load=Mock(return_value=state), save=Mock())

        app_main._mark_approved_signals_alerted(store, {"new-key"}, now=now)

        self.assertEqual(
            state.approved_signal_alerts,
            {
                "fresh": fresh_time,
                "new-key": now.isoformat(),
            },
        )
        store.save.assert_called_once()

    def test_scheduled_scan_keeps_general_notifications_when_vip_channel_has_no_approved_signal(self) -> None:
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
                candidate_signals=[],
                chat_ids=[101],
                approved_signal_alerts={},
            )
            store = SimpleNamespace(
                consume_scan_request=Mock(return_value=(state, False)),
                load=Mock(return_value=state),
            )
            bot = SimpleNamespace(send_message=AsyncMock())
            job_queue = SimpleNamespace(run_once=Mock())
            context = SimpleNamespace(
                application=SimpleNamespace(bot_data={"settings": settings, "store": store}),
                job_queue=job_queue,
                bot=bot,
            )

            with (
                patch("src.main._notification_chat_ids", return_value={101}),
                patch("src.main._approved_signal_chat_ids", return_value={202}),
                patch("src.main._approved_signals_to_alert", return_value=[]),
                patch("src.main._telegram_vip", return_value=SimpleNamespace(record_dispatch=Mock())),
                patch("src.main.PortalStore") as portal_store_mock,
                patch("src.main._scanner_cycle_seconds", return_value=20),
                patch("src.main.red_stop_status", return_value={}),
                patch("src.main._maybe_send_assisted_monitor_alert", new_callable=AsyncMock),
                patch("src.main.run_scan", new_callable=AsyncMock, return_value="scan-ok"),
            ):
                portal_store_mock.return_value.notification_scan_preferences.return_value = (20, 20)
                await app_main.scheduled_scan(context)

            bot.send_message.assert_awaited_once()
            self.assertEqual(bot.send_message.await_args.kwargs["chat_id"], 101)
            self.assertEqual(bot.send_message.await_args.kwargs["text"], "scan-ok")

        import asyncio

        asyncio.run(run_case())

    def test_passive_service_cycle_keeps_scanner_running_without_telegram(self) -> None:
        async def run_case() -> None:
            settings = SimpleNamespace(
                idle_scan_interval_seconds=20,
                active_scan_interval_seconds=20,
                portal_db_file="data/portal.db",
            )
            state = SimpleNamespace(
                active_game_id=None,
                active_signal=None,
                candidate_signals=[],
                history=[],
            )
            store = SimpleNamespace(
                consume_scan_request=Mock(return_value=(state, False)),
                load=Mock(return_value=state),
            )
            context = SimpleNamespace(
                application=SimpleNamespace(
                    bot_data={
                        "settings": settings,
                        "store": store,
                        "provider": object(),
                        "supabase": object(),
                    }
                )
            )

            with (
                patch("src.main.PortalStore") as portal_store_mock,
                patch("src.main._scanner_cycle_seconds", return_value=20),
                patch("src.main.run_scan", new_callable=AsyncMock, return_value="scan-ok") as run_scan_mock,
                patch("src.main.refresh_active_signal", new_callable=AsyncMock) as refresh_mock,
            ):
                portal_store_mock.return_value.notification_scan_preferences.return_value = (20, 20)
                interval = await app_main._passive_service_cycle(context)

            self.assertEqual(interval, 20)
            run_scan_mock.assert_awaited_once_with(context, auto_pick=False)
            refresh_mock.assert_not_awaited()

        import asyncio

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
