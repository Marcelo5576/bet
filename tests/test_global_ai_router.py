from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from services.globalAdaptiveIntelligence.core import GlobalAdaptiveIntelligencePlatform
from services.globalAdaptiveIntelligence.sports.football_adapter import FootballAdapter
from services.footballQuantAiSkill.schemas import BacktestSummary, BankrollAdvice, MatchPrediction
from src.dashboard import app
from src.global_ai_router import _GlobalDependencyFailure, _global, _require_admin, _require_user


class GlobalAIRouterTests(unittest.TestCase):
    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_football_adapter_prediction_serializes_slots_dataclass(self) -> None:
        prediction = MatchPrediction(
            match_id=10,
            market="match_winner_home",
            recommendation="NO_BET",
            confidence_score=64.5,
            risk_level="moderado",
            estimated_probability=0.57,
            fair_odd=1.75,
            offered_odd=1.9,
            expected_value=0.04,
            value_band="quase",
            explanation={"note": "ok"},
            bankroll=BankrollAdvice(
                bankroll=1000.0,
                profile="moderado",
                profile_multiplier=1.0,
                stake_fraction=0.01,
                suggested_stake=10.0,
                max_stake_cap=20.0,
                kelly_fraction=0.25,
                allowed=True,
                reason="ok",
            ),
            model_version="baseline",
            created_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
        )
        adapter = FootballAdapter.__new__(FootballAdapter)
        adapter.skill = SimpleNamespace(
            prediction=SimpleNamespace(predict_match=lambda *args, **kwargs: prediction),
            backtesting=None,
        )

        payload = adapter.runPrediction(10)

        self.assertEqual(payload["match_id"], 10)
        self.assertEqual(payload["bankroll"]["suggested_stake"], 10.0)
        self.assertEqual(payload["created_at"], prediction.created_at)

    def test_global_platform_backtest_serializes_slots_dataclass(self) -> None:
        summary = BacktestSummary(
            simulation_run_id=1,
            total_games=20,
            total_entries=8,
            hit_rate=55.0,
            roi=7.5,
            profit_loss=75.0,
            initial_bankroll=1000.0,
            final_bankroll=1075.0,
            drawdown_max=45.0,
            by_league=[],
            by_market=[],
            by_odds_range=[],
            by_ev_band=[],
        )
        platform = GlobalAdaptiveIntelligencePlatform.__new__(GlobalAdaptiveIntelligencePlatform)
        platform.settings = SimpleNamespace(default_market="match_winner_home", min_ev=0.03, min_confidence=60.0)
        platform.football_skill = SimpleNamespace(
            backtesting=SimpleNamespace(runBacktest=lambda request: summary)
        )

        payload = platform.run_backtest({})

        self.assertEqual(payload["simulation_run_id"], 1)
        self.assertEqual(payload["final_bankroll"], 1075.0)

    def test_global_ai_router_json_encodes_datetime_payloads(self) -> None:
        class StubPlatform:
            def football_analysis_board(self, *, user_id=None):
                return {
                    "market": "match_winner_home",
                    "research_health": {"counts": {"historical_matches": 3}, "supabase": {"enabled": True, "last_error": None}},
                    "items": [
                        {
                            "match": {"id": 1, "home_team": "A", "away_team": "B", "match_date": datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)},
                            "prediction": {"created_at": datetime(2026, 5, 7, 12, 1, tzinfo=timezone.utc), "confidence_score": 70},
                            "consensus": {"final_decision": "NO_BET"},
                            "risk": {"risk_score": 20},
                            "meta": {"selected_model": "baseline"},
                        }
                    ],
                }

        app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
        app.dependency_overrides[_global] = lambda: StubPlatform()
        client = TestClient(app)

        response = client.get("/api/global-ai/football-analysis")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["items"][0]["prediction"]["created_at"], "2026-05-07T12:01:00+00:00")
        self.assertEqual(payload["items"][0]["match"]["match_date"], "2026-05-07T12:00:00+00:00")
        self.assertEqual(payload["research_health"]["counts"]["historical_matches"], 3)

    def test_monte_carlo_page_keeps_inline_button_action(self) -> None:
        app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
        client = TestClient(app)

        response = client.get("/app/monte-carlo-lab")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="mc-run"', response.text)
        self.assertIn('onclick="runMonteCarlo(this); return false;"', response.text)
        self.assertIn("withBusy(button, 'Rodando...'", response.text)

    def test_football_analysis_page_surfaces_history_card(self) -> None:
        app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
        client = TestClient(app)

        response = client.get("/app/football-analysis")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Base historica usada nesta leitura", response.text)
        self.assertIn("Historico local", response.text)
        self.assertIn("Fontes ao vivo e odds reais", response.text)

    def test_bias_page_surfaces_history_context_copy(self) -> None:
        app.dependency_overrides[_require_admin] = lambda: {"id": 1, "email": "test@example.com"}
        client = TestClient(app)

        response = client.get("/app/market-bias-anomaly-center")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Historico usado para detectar", response.text)
        self.assertIn("loadBiasAnomaly", response.text)
        self.assertIn("Fontes ao vivo e odds reais", response.text)

    def test_global_ai_shell_handles_auth_expiry_and_admin_access_gracefully(self) -> None:
        app.dependency_overrides[_require_admin] = lambda: {"id": 1, "email": "test@example.com"}
        client = TestClient(app)

        response = client.get("/app/market-bias-anomaly-center")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Sessao expirada", response.text)
        self.assertIn("Acesso restrito", response.text)
        self.assertIn("Entrar novamente", response.text)

    def test_feature_lab_endpoint_includes_research_health(self) -> None:
        class StubRepo:
            def list_generated_features(self, limit=80):
                return [{"feature_name": "rolling::Serie A"}]

            def snapshot(self):
                return {"counts": {"generated_features": 1}}

        class StubPlatform:
            repository = StubRepo()

            def research_health_snapshot(self):
                return {"counts": {"historical_matches": 12}, "supabase": {"enabled": False}}

        app.dependency_overrides[_require_admin] = lambda: {"id": 1, "email": "test@example.com"}
        app.dependency_overrides[_global] = lambda: StubPlatform()
        client = TestClient(app)

        response = client.get("/api/global-ai/feature-lab")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["research_health"]["counts"]["historical_matches"], 12)
        self.assertEqual(payload["features"][0]["feature_name"], "rolling::Serie A")

    def test_football_analysis_endpoint_exposes_live_source_runtime(self) -> None:
        class StubPlatform:
            def football_analysis_board(self, *, user_id=None):
                return {
                    "market": "match_winner_home",
                    "items": [],
                    "research_health": {"counts": {"historical_matches": 3}},
                    "live_sources": [
                        {"id": "espn", "label": "ESPN Scoreboard", "status": "ready"},
                        {"id": "isports", "label": "iSports Odds", "status": "ready"},
                    ],
                }

        app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
        app.dependency_overrides[_global] = lambda: StubPlatform()
        client = TestClient(app)

        response = client.get("/api/global-ai/football-analysis")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["live_sources"][0]["label"], "ESPN Scoreboard")
        self.assertEqual(payload["live_sources"][1]["label"], "iSports Odds")

    def test_football_analysis_degrades_when_global_dependency_is_unavailable(self) -> None:
        app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
        app.dependency_overrides[_global] = lambda: _GlobalDependencyFailure(RuntimeError("boom"))
        client = TestClient(app)

        response = client.get("/api/global-ai/football-analysis")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["research_health"]["supabase"]["schema_mode"], "unavailable")
        self.assertEqual(payload["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
