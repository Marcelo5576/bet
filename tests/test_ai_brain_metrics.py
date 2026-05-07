from __future__ import annotations

import gc
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.ai_brain.brain_metrics_service import BrainMetricsService
from src.ai_brain_router import _service, router
from src.config import load_settings
from src.portal_web import _require_user


def _touch_db(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path.as_posix())
    try:
        conn.executescript(script)
        conn.commit()
    finally:
        conn.close()


class AiBrainMetricsServiceTest(unittest.TestCase):
    def test_without_databases_does_not_invent_metrics(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            service = BrainMetricsService(
                settings=load_settings(),
                research_db_file=base / "missing_research.db",
                brain_db_file=base / "missing_brain.db",
                decision_db_file=base / "missing_decision.db",
                usage_db_file=base / "missing_usage.db",
                global_ai_db_file=base / "missing_global.db",
            )
            payload = service.metrics()

        self.assertEqual(payload["status"], "Offline")
        self.assertEqual(payload["metrics"]["total_jogos_analisados"], 0)
        self.assertIsNone(payload["metrics"]["ROI_simulado"])
        self.assertIn("Nenhum banco", service.summary()["summary"] or "")

    def test_with_real_rows_calculates_maturity_and_recommendations(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            research = base / "research.db"
            brain = base / "brain.db"
            decision = base / "decision.db"
            usage = base / "usage.db"
            global_ai = base / "global.db"
            _touch_db(
                research,
                """
                CREATE TABLE historical_matches (id INTEGER PRIMARY KEY, league TEXT, match_date TEXT, imported_at TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE historical_odds (historical_match_id INTEGER, market TEXT, is_real INTEGER);
                CREATE TABLE historical_stats (historical_match_id INTEGER, possession_home REAL, shots_home INTEGER, shots_on_home INTEGER, corners_home INTEGER, yellow_home INTEGER, dangerous_attacks_home INTEGER, xg_home REAL);
                CREATE TABLE historical_features (id INTEGER PRIMARY KEY);
                CREATE TABLE predictions (id INTEGER PRIMARY KEY, market TEXT);
                CREATE TABLE simulation_runs (id INTEGER PRIMARY KEY);
                CREATE TABLE simulation_results (id INTEGER PRIMARY KEY, historical_match_id INTEGER, market TEXT, result TEXT, stake REAL, profit_loss REAL, expected_value REAL, offered_odd REAL, created_at TEXT);
                CREATE TABLE learning_events (payload_json TEXT, created_at TEXT);
                CREATE TABLE rag_documents (id INTEGER PRIMARY KEY);
                CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY);
                CREATE TABLE league_reliability_scores (league TEXT, season INTEGER, classification TEXT, league_reliability_score REAL, avg_data_quality REAL, odds_count INTEGER, stats_count INTEGER, match_count INTEGER);
                CREATE TABLE football_research_logs (created_at TEXT);
                INSERT INTO historical_matches VALUES (1,'Liga A','2026-01-01','2026-05-06','2026-05-06','2026-05-06');
                INSERT INTO historical_matches VALUES (2,'Liga A','2026-01-02','2026-05-06','2026-05-06','2026-05-06');
                INSERT INTO historical_odds VALUES (1,'over_2_5',1);
                INSERT INTO historical_features VALUES (1);
                INSERT INTO historical_features VALUES (2);
                INSERT INTO predictions VALUES (1,'over_2_5');
                INSERT INTO simulation_runs VALUES (1);
                INSERT INTO simulation_results VALUES (1,1,'over_2_5','WIN',10,8,0.08,1.8,'2026-05-06T10:00:00+00:00');
                INSERT INTO simulation_results VALUES (2,2,'over_2_5','LOSS',10,-10,0.04,1.9,'2026-05-06T11:00:00+00:00');
                INSERT INTO learning_events VALUES ('{"brier_score":0.22}','2026-05-06T12:00:00+00:00');
                INSERT INTO league_reliability_scores VALUES ('Liga A',2026,'Em observação',44,65,1,0,2);
                """,
            )
            _touch_db(
                brain,
                """
                CREATE TABLE brain_matches (match_id TEXT, league TEXT, last_seen_at TEXT);
                CREATE TABLE brain_live_snapshots (captured_at TEXT);
                CREATE TABLE brain_pregame_watchlist (recorded_at TEXT);
                CREATE TABLE brain_skill_results (captured_at TEXT, decision TEXT, market TEXT);
                INSERT INTO brain_matches VALUES ('m1','Liga A','2026-05-06T12:01:00+00:00');
                INSERT INTO brain_skill_results VALUES ('2026-05-06T12:02:00+00:00','ENTRA','Gols');
                """,
            )
            _touch_db(
                decision,
                """
                CREATE TABLE decision_logs (match_id TEXT, league TEXT, market TEXT, entry_allowed INTEGER, created_at TEXT);
                CREATE TABLE backtest_runs (created_at TEXT);
                INSERT INTO decision_logs VALUES ('m1','Liga A','1X2',1,'2026-05-06T12:03:00+00:00');
                INSERT INTO decision_logs VALUES ('m2','Liga B','1X2',0,'2026-05-06T12:04:00+00:00');
                """,
            )
            _touch_db(
                usage,
                """
                CREATE TABLE service_usage_totals (service TEXT, requests INTEGER, success_requests INTEGER, last_request_at TEXT, last_error TEXT);
                INSERT INTO service_usage_totals VALUES ('api_football',4,4,'2026-05-06T12:05:00+00:00',NULL);
                """,
            )
            _touch_db(
                global_ai,
                """
                CREATE TABLE monte_carlo_runs (id INTEGER PRIMARY KEY);
                CREATE TABLE long_term_memory (id INTEGER PRIMARY KEY);
                CREATE TABLE agent_trust_scores (id INTEGER PRIMARY KEY);
                INSERT INTO long_term_memory VALUES (1);
                """,
            )
            service = BrainMetricsService(
                settings=load_settings(),
                research_db_file=research,
                brain_db_file=brain,
                decision_db_file=decision,
                usage_db_file=usage,
                global_ai_db_file=global_ai,
            )
            payload = service.metrics()
            gc.collect()

        self.assertEqual(payload["metrics"]["total_backtests"], 1)
        self.assertEqual(payload["metrics"]["dados_com_odds_confirmadas"], 1)
        self.assertEqual(payload["metrics"]["brier_score_medio"], 0.22)
        self.assertEqual(payload["metrics"]["entradas_liberadas"], 2)
        self.assertTrue(payload["recommendations"])

    def test_live_scanner_data_with_provider_errors_is_learning_not_offline(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            research = base / "research.db"
            brain = base / "brain.db"
            decision = base / "decision.db"
            usage = base / "usage.db"
            _touch_db(
                brain,
                """
                CREATE TABLE brain_matches (match_id TEXT, league TEXT, last_seen_at TEXT);
                CREATE TABLE brain_live_snapshots (captured_at TEXT);
                CREATE TABLE brain_pregame_watchlist (recorded_at TEXT);
                CREATE TABLE brain_learning_events (id INTEGER PRIMARY KEY, created_at TEXT);
                CREATE TABLE brain_skill_results (captured_at TEXT, decision TEXT, market TEXT);
                INSERT INTO brain_matches VALUES ('m1','Liga A','2026-05-06T12:01:00+00:00');
                INSERT INTO brain_matches VALUES ('m2','Liga A','2026-05-06T12:01:00+00:00');
                INSERT INTO brain_learning_events VALUES (1,'2026-05-06T12:01:00+00:00');
                """,
            )
            _touch_db(
                decision,
                """
                CREATE TABLE decision_logs (match_id TEXT, league TEXT, market TEXT, entry_allowed INTEGER, created_at TEXT);
                CREATE TABLE backtest_runs (created_at TEXT);
                """ + "\n".join(
                    f"INSERT INTO decision_logs VALUES ('m{i}','Liga A','1X2',{1 if i % 2 else 0},'2026-05-06T12:04:00+00:00');"
                    for i in range(1, 130)
                ),
            )
            _touch_db(
                usage,
                """
                CREATE TABLE service_usage_totals (service TEXT, requests INTEGER, success_requests INTEGER, last_request_at TEXT, last_error TEXT);
                INSERT INTO service_usage_totals VALUES ('api_football',10,10,'2026-05-06T12:05:00+00:00',NULL);
                INSERT INTO service_usage_totals VALUES ('gemini',10,8,'2026-05-06T12:05:00+00:00','Gemini em 429 por excesso de chamadas.');
                INSERT INTO service_usage_totals VALUES ('odds_api_io',10,8,'2026-05-06T12:05:00+00:00','Odds em 429.');
                """,
            )
            with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-key", "API_FOOTBALL_KEY": "api-key", "ODDS_API_IO_KEY": "odds-key"}, clear=False):
                service = BrainMetricsService(
                    settings=load_settings(),
                    research_db_file=research,
                    brain_db_file=brain,
                    decision_db_file=decision,
                    usage_db_file=usage,
                    global_ai_db_file=base / "missing_global.db",
                )
                payload = service.metrics()

        self.assertEqual(payload["status"], "Aprendendo")
        self.assertIn("falta consolidar", payload["status_reason"])
        self.assertGreater(payload["metrics"]["total_jogos_analisados"], 100)
        self.assertEqual(payload["metrics"]["total_jogos_historicos"], 0)
        self.assertGreater(payload["metrics"]["dados_sem_odds"], 100)

    def test_metrics_route_never_returns_configured_secrets(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            secret = "super-secret-gemini-token"
            with patch.dict(os.environ, {"GEMINI_API_KEY": secret, "API_FOOTBALL_KEY": "api-secret"}, clear=False):
                service = BrainMetricsService(
                    settings=load_settings(),
                    research_db_file=base / "missing_research.db",
                    brain_db_file=base / "missing_brain.db",
                    decision_db_file=base / "missing_decision.db",
                    usage_db_file=base / "missing_usage.db",
                    global_ai_db_file=base / "missing_global.db",
                )
                app = FastAPI()
                app.include_router(router)
                app.dependency_overrides[_require_user] = lambda: {"id": 1, "email": "test@example.com"}
                app.dependency_overrides[_service] = lambda: service
                client = TestClient(app)
                response = client.get("/api/ai-brain/metrics")

        self.assertEqual(response.status_code, 200)
        text = json.dumps(response.json(), ensure_ascii=False)
        self.assertNotIn(secret, text)
        self.assertNotIn("api-secret", text)


if __name__ == "__main__":
    unittest.main()
