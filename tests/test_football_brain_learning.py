from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.intelligence.football_brain import FootballBrain


def _game(minute: int, home_goals: int, away_goals: int) -> dict:
    return {
        "game_id": "fixture-1",
        "league": "Liga Teste",
        "home": "Casa",
        "away": "Fora",
        "status": "live",
        "minute": minute,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_pressure": 72,
        "away_pressure": 61,
        "home_shots_on": 3,
        "away_shots_on": 2,
        "markets": {
            "live_facts": {
                "shots_home": 8,
                "shots_away": 6,
                "shots_on_home": 3,
                "shots_on_away": 2,
                "corners_home": 4,
                "corners_away": 3,
                "dangerous_attacks_home": 28,
                "dangerous_attacks_away": 21,
            },
            "goals": {
                "over": {"line": "2.5", "odds": 1.95},
                "under": {"line": "2.5", "odds": 1.85},
            },
        },
    }


class FootballBrainLearningTests(unittest.TestCase):
    def test_harvests_goal_next_10_learning_event_from_real_later_snapshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            brain = FootballBrain(str(Path(tmp) / "brain.db"))

            brain.record_live_games([_game(10, 0, 0)], source="test")
            self.assertEqual(brain.status()["learning_events"], 0)

            brain.record_live_games([_game(22, 1, 0)], source="test")
            status = brain.status()

            self.assertEqual(status["learning_events"], 1)
            self.assertEqual(status["learning_summary"][0]["market"], "GOAL_NEXT_10")
            self.assertEqual(status["learning_summary"][0]["greens"], 1)
            self.assertEqual(status["learning_summary"][0]["reds"], 0)

            brain.record_live_games([_game(23, 1, 0)], source="test")
            self.assertEqual(brain.status()["learning_events"], 1)


if __name__ == "__main__":
    unittest.main()
