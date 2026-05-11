from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.storage import StateStore


class StateStoreSignalLookupTests(unittest.TestCase):
    def test_get_signal_finds_candidate_and_active_signal(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = StateStore(str(Path(tmp) / "state.json"))
            signal = {
                "signal_id": "sig-1",
                "game": {"game_id": "game-1", "home": "Flamengo", "away": "Palmeiras"},
                "market": "Gols",
            }

            store.set_candidates([signal])
            self.assertEqual(store.get_signal("sig-1")["market"], "Gols")

            store.choose_candidate(0)
            self.assertEqual(store.get_signal("sig-1")["game"]["game_id"], "game-1")

    def test_update_signal_fields_updates_candidate_history_and_active(self) -> None:
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = StateStore(str(Path(tmp) / "state.json"))
            signal = {
                "signal_id": "sig-2",
                "game": {"game_id": "game-2", "home": "Santos", "away": "Bahia"},
                "market": "Gols",
            }
            store.set_candidates([signal])

            state, updated = store.update_signal_fields(
                "sig-2",
                {"status": "prepared_waiting_manual_confirmation", "bet365_current_odd": 1.72},
                activate=True,
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "prepared_waiting_manual_confirmation")
            self.assertEqual(state.active_game_id, "game-2")
            self.assertEqual(store.get_signal("sig-2")["bet365_current_odd"], 1.72)


if __name__ == "__main__":
    unittest.main()
