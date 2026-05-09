from __future__ import annotations

from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock

from src.executors.bet365_assisted import (
    _OPEN_ASSISTED_SESSIONS,
    assisted_session_snapshot,
    build_prepare_request_from_signal,
    close_assisted_session,
    confirm_prepared_signal,
    persist_prepare_response,
)
from src.models.executor_models import PrepareBet365Request, PrepareBet365Response
from src.storage import StateStore


class Bet365AssistedHelpersTest(unittest.TestCase):
    def test_build_prepare_request_from_signal_uses_signal_fields(self) -> None:
        signal = {
            "signal_id": "SIG-123",
            "market": "Total de Gols",
            "selection": "Mais de 1.5",
            "target_odds": 1.55,
            "stake_value": 10.0,
            "game": {
                "home": "Flamengo",
                "away": "Palmeiras",
            },
        }

        request = build_prepare_request_from_signal(signal)

        self.assertEqual(request.signal_id, "SIG-123")
        self.assertEqual(request.match_name, "Flamengo x Palmeiras")
        self.assertEqual(request.market, "Total de Gols")
        self.assertEqual(request.selection, "Mais de 1.5")
        self.assertEqual(request.min_odd, 1.55)
        self.assertEqual(request.stake, 10.0)

    def test_persist_prepare_response_marks_waiting_manual_confirmation(self) -> None:
        with TemporaryDirectory() as tmp:
            store = StateStore(f"{tmp}/state.json")
            store.set_active(
                "fixture-1",
                {
                    "signal_id": "SIG-123",
                    "market": "Total de Gols",
                    "selection": "Mais de 1.5",
                    "game": {"game_id": "fixture-1", "home": "Flamengo", "away": "Palmeiras"},
                },
            )
            request = PrepareBet365Request(
                match_name="Flamengo x Palmeiras",
                market="Total de Gols",
                selection="Mais de 1.5",
                min_odd=1.55,
                stake=10.0,
                signal_id="SIG-123",
            )
            response = PrepareBet365Response(
                ok=True,
                status="prepared",
                message="Entrada preparada na Bet365. Confirme manualmente.",
                current_odd=1.62,
                screenshot_path="/tmp/bet365_SIG-123.png",
                page_url="https://www.bet365.com/#/AC/B1/C1/D13/E181645/F2/",
                signal_id="SIG-123",
            )

            _, signal = persist_prepare_response(store, request, response, assisted_chat_id=6704344864)

            self.assertIsNotNone(signal)
            self.assertEqual(signal["status"], "prepared_waiting_manual_confirmation")
            self.assertEqual(signal["entry_odds"], 1.62)
            self.assertEqual(signal["stake_value"], 10.0)
            self.assertEqual(signal["assisted_chat_id"], 6704344864)
            self.assertEqual(signal["assisted_page_url"], "https://www.bet365.com/#/AC/B1/C1/D13/E181645/F2/")

    def test_confirm_prepared_signal_marks_position_open(self) -> None:
        with TemporaryDirectory() as tmp:
            store = StateStore(f"{tmp}/state.json")
            store.set_active(
                "fixture-1",
                {
                    "signal_id": "SIG-123",
                    "status": "prepared_waiting_manual_confirmation",
                    "entered": False,
                    "game": {"game_id": "fixture-1", "home": "Flamengo", "away": "Palmeiras"},
                },
            )

            _, signal = confirm_prepared_signal(store, "SIG-123", assisted_chat_id=6704344864)

            self.assertIsNotNone(signal)
            self.assertEqual(signal["status"], "position_open")
            self.assertTrue(signal["entered"])
            self.assertEqual(signal["assisted_chat_id"], 6704344864)
            self.assertTrue(signal.get("entered_at"))


class Bet365AssistedSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        _OPEN_ASSISTED_SESSIONS.clear()

    async def test_assisted_session_snapshot_lists_open_signal_ids(self) -> None:
        fake_page = type("FakePage", (), {"is_closed": lambda self: False, "url": "https://www.bet365.com/event"})()
        _OPEN_ASSISTED_SESSIONS["SIG-123"] = {
            "playwright": object(),
            "context": object(),
            "page": fake_page,
            "updated_at": "2026-05-09T12:00:00+00:00",
        }

        snapshot = assisted_session_snapshot()

        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["signal_ids"], ["SIG-123"])
        self.assertEqual(snapshot["sessions"][0]["page_url"], "https://www.bet365.com/event")
        self.assertTrue(snapshot["sessions"][0]["page_open"])

    async def test_close_assisted_session_closes_context_and_playwright(self) -> None:
        context = AsyncMock()
        playwright = AsyncMock()
        fake_page = type("FakePage", (), {"is_closed": lambda self: False, "url": "https://www.bet365.com/event"})()
        _OPEN_ASSISTED_SESSIONS["SIG-123"] = {
            "playwright": playwright,
            "context": context,
            "page": fake_page,
            "updated_at": "2026-05-09T12:00:00+00:00",
        }

        closed = await close_assisted_session("SIG-123")

        self.assertTrue(closed)
        context.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        self.assertEqual(_OPEN_ASSISTED_SESSIONS, {})
