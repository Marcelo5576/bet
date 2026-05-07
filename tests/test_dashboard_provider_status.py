import unittest

from src.dashboard import (
    _football_provider_status_label,
    _provider_dashboard_strings,
    _provider_live_metrics,
    _provider_status_with_live,
)


class DashboardProviderStatusTests(unittest.TestCase):
    def test_provider_live_metrics_counts_confirmed_live_fixtures(self):
        live_games = [
            {"game_id": "1", "odds_confirmed": True},
            {"game_id": "2", "odds_confirmed": False},
            {"game_id": "3", "odds_confirmed": True},
        ]

        metrics = _provider_live_metrics(live_games)

        self.assertEqual(metrics["live_fixture_count"], 3)
        self.assertEqual(metrics["live_fixtures_with_confirmed_odds"], 2)
        self.assertAlmostEqual(metrics["odds_coverage_ratio"], 0.6667, places=4)

    def test_provider_status_uses_live_fixture_count_not_raw_payload_items(self):
        status = {
            "configured": True,
            "active": True,
            "fallback_active": False,
            "last_http_status": 200,
            "last_payload_items": 1224,
        }
        live_games = [
            {"game_id": "1", "odds_confirmed": True},
            {"game_id": "2", "odds_confirmed": False},
        ]

        merged = _provider_status_with_live(status, live_games)

        self.assertEqual(merged["games_imported"], 2)
        self.assertEqual(merged["raw_payload_items"], 1224)
        self.assertEqual(merged["live_fixture_count"], 2)
        self.assertEqual(merged["live_fixtures_with_confirmed_odds"], 1)

    def test_provider_dashboard_strings_reports_partial_live_coverage(self):
        payload = {
            "configured": True,
            "active": True,
            "fallback_active": False,
            "last_http_status": 200,
            "live_fixture_count": 22,
            "live_fixtures_with_confirmed_odds": 17,
            "odds_coverage_ratio": 17 / 22,
            "last_error": "",
        }

        summary = _provider_dashboard_strings(payload)

        self.assertEqual(summary["api_health"], "API: ativa e respondendo")
        self.assertEqual(summary["odds_text"], "🟡 Parcial: 17/22")
        self.assertEqual(summary["coverage"], "Cobertura: parcial 17/22 fixtures")
        self.assertIn("Alguns jogos têm odds reais", summary["note"])
        self.assertEqual(
            summary["recommendation"],
            "Provider complementar: opcional, API atual esta funcional",
        )

    def test_provider_dashboard_strings_reports_missing_key_clearly(self):
        payload = {
            "configured": False,
            "active": False,
            "fallback_active": False,
            "last_http_status": None,
            "live_fixture_count": 0,
            "live_fixtures_with_confirmed_odds": 0,
            "odds_coverage_ratio": 0.0,
            "last_error": "",
        }

        summary = _provider_dashboard_strings(payload)

        self.assertEqual(_football_provider_status_label(payload), "API-Football nao configurada")
        self.assertEqual(summary["api_health"], "API: nao configurada")
        self.assertEqual(summary["odds_text"], "⚪ Chave ausente")
        self.assertEqual(summary["coverage"], "Cobertura: provider nao configurado")
        self.assertIn("API_FOOTBALL_KEY ausente", summary["note"])
        self.assertIn("API_FOOTBALL_KEY", summary["recommendation"])


if __name__ == "__main__":
    unittest.main()
