from __future__ import annotations

import unittest

from services.footballQuantAiSkill.bankroll.bankroll_service import BankrollService
from services.footballQuantAiSkill.models.poisson_model_service import PoissonModelService
from services.footballQuantAiSkill.schemas import TeamContext
from services.footballQuantAiSkill.value_betting.value_bet_service import ValueBetService


class FoundationModelsTest(unittest.TestCase):
    def test_value_bet_blocks_negative_ev(self):
        result = ValueBetService().assess(estimated_probability=0.45, offered_odd=2.0, confidence_score=70)
        self.assertLessEqual(result.expected_value or 0, 0)
        self.assertFalse(result.allowed)

    def test_kelly_caps_stake_at_three_percent(self):
        advice = BankrollService().recommend(bankroll=1000, probability=0.7, odd=2.2, profile="agressivo")
        self.assertLessEqual(advice.suggested_stake, 30.0)
        self.assertTrue(advice.allowed)

    def test_poisson_probabilities_stay_in_range(self):
        home = TeamContext(team="Casa", league="Liga", sample_size=10, goals_for_avg_5=1.8, goals_against_avg_5=1.0)
        away = TeamContext(team="Fora", league="Liga", sample_size=10, goals_for_avg_5=1.1, goals_against_avg_5=1.4)
        baseline = {"home_goals_avg": 1.35, "away_goals_avg": 1.05, "total_goals_avg": 2.4}
        prediction = PoissonModelService().predict(home, away, baseline)
        self.assertGreaterEqual(prediction.home_win, 0)
        self.assertLessEqual(prediction.home_win, 1)
        total_mass = prediction.home_win + prediction.draw + prediction.away_win
        self.assertGreater(total_mass, 0.97)
        self.assertLessEqual(total_mass, 1.0)


if __name__ == "__main__":
    unittest.main()
