from __future__ import annotations

import unittest
from unittest.mock import patch

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

    def test_does_not_show_entry_when_confidence_below_monitor_cut(self):
        decision = build_decision_view_model(
            _base_signal(confidence=42, confidence_score=0.42, entry_allowed=False)
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

    def test_legacy_enter_action_cannot_override_failed_criteria(self):
        decision = build_decision_view_model(
            _base_signal(
                action="ENTRAR",
                confidence=52,
                confidence_score=0.52,
                final_score=45,
                entry_score=45,
                entry_allowed=True,
            )
        )
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertNotEqual(decision["action_label"], "ENTRAR AGORA")

    def test_negative_historical_1x2_home_roi_blocks_enter_now(self):
        with patch(
            "src.intelligence.scanner_presentation._market_learning_guard",
            return_value={
                "active": True,
                "market": "match_winner_home",
                "label": "1X2 casa",
                "entries": 16,
                "roi_on_staked": -50.29,
                "reason": "1X2 casa rebaixado: odds historicas reais mostram ROI paper -50.3% em 16 entradas.",
            },
        ):
            decision = build_decision_view_model(
                _base_signal(
                    market="Resultado Final",
                    selection="Time A",
                    entry_allowed=True,
                    confidence=82,
                    confidence_score=0.82,
                    entry_score=78,
                    final_score=78,
                    expected_value=0.11,
                    target_odds=1.91,
                    entry_odds=1.91,
                )
            )
        self.assertEqual(decision["decision_status"], "DO_NOT_ENTER")
        self.assertFalse(decision["entry_allowed"])
        self.assertIn("1X2 casa rebaixado", decision["main_reason"])
        self.assertIn("❌ Histórico 1X2 ruim", [item["text"] for item in decision["checklist"]])

    def test_returns_full_decision_view_model_shape(self):
        decision = build_decision_view_model(_base_signal())
        for key in (
            "decision_status",
            "decision_label",
            "decision_emoji",
            "decision_color",
            "action_label",
            "main_reason",
            "risk_level",
            "risk_color",
            "confidence_label",
            "score_label",
            "ev_label",
            "checklist",
            "blocking_reasons",
            "positive_reasons",
            "card_priority",
        ):
            self.assertIn(key, decision)


if __name__ == "__main__":
    unittest.main()
