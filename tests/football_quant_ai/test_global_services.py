from __future__ import annotations

import unittest

from services.globalAdaptiveIntelligence.ensemble.ensemble_prediction_service import EnsemblePredictionService
from services.globalAdaptiveIntelligence.meta_learning.meta_learning_service import MetaLearningService
from services.globalAdaptiveIntelligence.multi_agent.consensus_engine import ConsensusEngine
from services.globalAdaptiveIntelligence.monte_carlo.monte_carlo_engine import MonteCarloEngine
from services.globalAdaptiveIntelligence.governance.governance_service import GovernanceService
from services.globalAdaptiveIntelligence.repository import GlobalAdaptiveRepository


class GlobalServicesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = GlobalAdaptiveRepository("data/test_global_adaptive.db")

    def test_ensemble_combines_model_outputs(self):
        service = EnsemblePredictionService(self.repo)
        result = service.combine(
            [
                {"model_name": "A", "estimated_probability": 0.6, "confidence_score": 70, "explanation": "A"},
                {"model_name": "B", "estimated_probability": 0.4, "confidence_score": 50, "explanation": "B"},
            ]
        )
        self.assertGreater(result["estimated_probability"], 0.45)
        self.assertLess(result["estimated_probability"], 0.61)

    def test_meta_learning_selects_highest_confidence(self):
        service = MetaLearningService(self.repo)
        decision = service.select_model(
            sport_or_market="football",
            league="Liga",
            market="match_winner_home",
            data_quality=80,
            model_outputs=[
                {"model_name": "slow", "estimated_probability": 0.51, "confidence_score": 60},
                {"model_name": "fast", "estimated_probability": 0.57, "confidence_score": 75},
            ],
        )
        self.assertEqual(decision["selected_model"], "fast")

    def test_consensus_prefers_weighted_majority(self):
        engine = ConsensusEngine()
        result = engine.decide(
            [
                {"agent_name": "A", "decision": "ENTRA_FORTE", "trust_score": 0.8, "reason": "A"},
                {"agent_name": "B", "decision": "NO_BET", "trust_score": 0.3, "reason": "B"},
                {"agent_name": "C", "decision": "ENTRA_FORTE", "trust_score": 0.5, "reason": "C"},
            ]
        )
        self.assertEqual(result["final_decision"], "ENTRA_FORTE")

    def test_monte_carlo_returns_paths_and_ruin_risk(self):
        result = MonteCarloEngine(seed=1).run(
            hit_rate=0.55,
            average_odd=1.9,
            bankroll=1000,
            stake_pct=0.015,
            paths=50,
            steps=20,
        )
        self.assertEqual(result["paths"], 50)
        self.assertGreaterEqual(result["ruin_risk"], 0)
        self.assertLessEqual(result["ruin_risk"], 1)

    def test_governance_creates_pending_request(self):
        service = GovernanceService(self.repo)
        request_id = service.request_change(
            change_type="strategy_activation",
            target_ref="strategy_version:1",
            payload={"strategy_version_id": 1},
        )
        snapshot = service.snapshot()
        pending_ids = [item["id"] for item in snapshot["pending"]]
        self.assertIn(request_id, pending_ids)


if __name__ == "__main__":
    unittest.main()

