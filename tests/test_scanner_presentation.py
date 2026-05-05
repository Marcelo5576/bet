from __future__ import annotations

import unittest

from src.intelligence.scanner_presentation import build_decision_view_model


def _base_signal(**overrides):
    payload = {
        "game": {
            "home": "Time A",
            "away": "Time B",
            "minute": 58,
            "home_goals": 0,
            "away_goals": 0,
            "league": "Serie A",
        },
        "market": "Over 1.5 gols",
        "target_odds": 2.03,
        "entry_odds": 2.03,
        "confidence": 68,
        "confidence_score": 0.68,
        "entry_score": 72,
        "final_score": 72,
        "expected_value": 0.09,
        "entry_allowed": True,
        "data_quality": 84,
        "risk_level": "Médio",
        "ai_explanation": "Confiança boa, score aprovado e odd dentro da faixa.",
    }
    payload.update(overrides)
    return payload


class ScannerPresentationTests(unittest.TestCase):
    def test_does_not_show_entry_when_score_below_65(self):
        decision = build_decision_view_model(_base_signal(entry_score=45, final_score=45, entry_allowed=False))
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertEqual(decision["action_label"], "NÃO ENTRAR")

    def test_does_not_show_entry_when_confidence_below_65(self):
        decision = build_decision_view_model(
            _base_signal(confidence=52, confidence_score=0.52, entry_allowed=False)
        )
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertEqual(decision["decision_label"], "NÃO ENTRAR")

    def test_does_not_show_entry_when_odd_below_minimum(self):
        decision = build_decision_view_model(
            _base_signal(target_odds=1.40, entry_odds=1.40, entry_allowed=False)
        )
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertIn("Odd muito baixa", decision["main_reason"])

    def test_shows_entry_allowed_when_all_criteria_pass(self):
        decision = build_decision_view_model(_base_signal())
        self.assertEqual(decision["decision_status"], "ENTER_NOW")
        self.assertEqual(decision["action_label"], "ENTRAR AGORA")

    def test_shows_wait_confirmation_when_close_to_threshold(self):
        decision = build_decision_view_model(
            _base_signal(
                confidence=66,
                confidence_score=0.66,
                entry_score=60,
                final_score=60,
                expected_value=0.04,
                entry_allowed=False,
                ai_explanation="Sinal promissor, mas ainda precisa confirmação.",
            )
        )
        self.assertEqual(decision["decision_status"], "WAIT_CONFIRMATION")
        self.assertEqual(decision["action_label"], "AGUARDAR CONFIRMAÇÃO")

    def test_shows_no_entry_when_ev_negative(self):
        decision = build_decision_view_model(
            _base_signal(expected_value=-0.02, entry_allowed=False)
        )
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertEqual(decision["action_label"], "NÃO ENTRAR")

    def test_shows_no_data_without_odds_and_stats(self):
        decision = build_decision_view_model(
            _base_signal(target_odds=None, entry_odds=None, entry_allowed=False, data_quality=30)
        )
        self.assertEqual(decision["decision_status"], "NO_DATA")
        self.assertEqual(decision["action_label"], "APENAS MONITORAR")

    def test_generates_checklist_and_reason(self):
        decision = build_decision_view_model(
            _base_signal(confidence=52, confidence_score=0.52, final_score=45, entry_score=45, entry_allowed=False)
        )
        checklist = [item["text"] for item in decision["checklist"]]
        self.assertIn("❌ Confiança baixa", checklist)
        self.assertIn("❌ Score baixo", checklist)
        self.assertIn("Não entrar", decision["main_reason"])


if __name__ == "__main__":
    unittest.main()
