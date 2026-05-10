from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import httpx

from services.footballQuantAiSkill.repository import FootballResearchRepository
from services.footballQuantAiSkill.supabase.research_sync_service import ResearchSupabaseSyncService


class _StubResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else str(payload)
        self.content = self.text.encode("utf-8")
        self.request = httpx.Request("GET", "https://example.test")

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request, text=self.text),
            )


class _LegacyAwareClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        table = url.rsplit("/", 1)[-1]
        params = params or {}
        select = str(params.get("select") or "")

        if table == "historical_matches":
            return _StubResponse(404, {"code": "PGRST205", "hint": "Talvez public.betsignal_games?"})
        if table == "historical_features":
            return _StubResponse(404, {"code": "PGRST205"})
        if table == "league_reliability_scores":
            return _StubResponse(404, {"code": "PGRST205"})
        if table == "betsignal_ai_memory":
            return _StubResponse(200, [])
        if table == "betsignal_ai_skills":
            return _StubResponse(200, [])
        if table == "betsignal_games" and select == "game_id":
            return _StubResponse(200, [{"game_id": "espn-1"}])
        if table == "betsignal_signals" and select == "signal_id":
            return _StubResponse(200, [{"signal_id": "sig-1"}])
        if table == "betsignal_games":
            return _StubResponse(
                200,
                [
                    {
                        "game_id": "espn-1",
                        "league": "Brazilian Serie A",
                        "division": "Brasil - Times brasileiros",
                        "home": "Time A",
                        "away": "Time B",
                        "minute": 90,
                        "home_goals": 2,
                        "away_goals": 1,
                        "home_pressure": 81,
                        "away_pressure": 54,
                        "home_shots_on": 7,
                        "away_shots_on": 4,
                        "odds_home": 1.87,
                        "odds_draw": 3.2,
                        "odds_away": 4.1,
                        "priority": 4,
                        "markets": {"1x2": {"home": 1.87}},
                        "raw": {"venue": "Arena"},
                        "updated_at": "2026-05-07T11:00:00+00:00",
                    }
                ],
            )
        if table == "betsignal_signals":
            return _StubResponse(
                200,
                [
                    {
                        "signal_id": "sig-1",
                        "game_id": "espn-1",
                        "market": "1X2",
                        "entry_market": "1X2",
                        "confidence": 78,
                        "target_odds": 1.9,
                        "entry_odds": 1.87,
                        "data_quality": 84,
                        "value_edge": 0.06,
                        "profit_units": 1.0,
                        "outcome": "green",
                        "payload": {"source": "scanner"},
                        "created_at": "2026-05-07T11:01:00+00:00",
                        "updated_at": "2026-05-07T11:02:00+00:00",
                    }
                ],
            )
        raise AssertionError(f"Unexpected table request: {table} params={params}")


class _HistoricalProbeBreaksButLegacyWorksClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        table = url.rsplit("/", 1)[-1]
        params = params or {}
        select = str(params.get("select") or "")

        if table == "historical_matches" and select == "id":
            return _StubResponse(200, [])
        if table == "historical_matches":
            return _StubResponse(404, {"code": "PGRST205", "message": "historical view incomplete"})
        if table == "historical_features":
            return _StubResponse(404, {"code": "PGRST205"})
        if table == "league_reliability_scores":
            return _StubResponse(404, {"code": "PGRST205"})
        if table == "betsignal_ai_memory":
            return _StubResponse(200, [])
        if table == "betsignal_ai_skills":
            return _StubResponse(200, [])
        if table == "betsignal_games" and select == "game_id":
            return _StubResponse(200, [{"game_id": "espn-1"}])
        if table == "betsignal_signals" and select == "signal_id":
            return _StubResponse(200, [{"signal_id": "sig-1"}])
        if table == "betsignal_games":
            return _StubResponse(
                200,
                [
                    {
                        "game_id": "espn-1",
                        "league": "Brazilian Serie A",
                        "division": "Brasil - Times brasileiros",
                        "home": "Time A",
                        "away": "Time B",
                        "minute": 90,
                        "home_goals": 2,
                        "away_goals": 1,
                        "home_pressure": 81,
                        "away_pressure": 54,
                        "home_shots_on": 7,
                        "away_shots_on": 4,
                        "odds_home": 1.87,
                        "odds_draw": 3.2,
                        "odds_away": 4.1,
                        "priority": 4,
                        "markets": {"1x2": {"home": 1.87}},
                        "raw": {"venue": "Arena"},
                        "updated_at": "2026-05-07T11:00:00+00:00",
                    }
                ],
            )
        if table == "betsignal_signals":
            return _StubResponse(
                200,
                [
                    {
                        "signal_id": "sig-1",
                        "game_id": "espn-1",
                        "market": "1X2",
                        "entry_market": "1X2",
                        "confidence": 78,
                        "target_odds": 1.9,
                        "entry_odds": 1.87,
                        "data_quality": 84,
                        "value_edge": 0.06,
                        "profit_units": 1.0,
                        "outcome": "green",
                        "payload": {"source": "scanner"},
                        "created_at": "2026-05-07T11:01:00+00:00",
                        "updated_at": "2026-05-07T11:02:00+00:00",
                    }
                ],
            )
        raise AssertionError(f"Unexpected table request: {table} params={params}")


class _MissingSchemaClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None, params=None):
        return _StubResponse(404, {"code": "PGRST205", "message": "missing"})


class ResearchSupabaseSyncServiceTests(unittest.TestCase):
    def test_hydrate_uses_legacy_betsignal_tables_when_new_schema_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FootballResearchRepository(f"{tmp}/research.db")
            service = ResearchSupabaseSyncService(
                repo,
                supabase_url="https://example.test",
                supabase_service_role_key="secret",
            )

            with patch("services.footballQuantAiSkill.supabase.research_sync_service.httpx.Client", return_value=_LegacyAwareClient()):
                result = service.hydrate_local_cache(match_limit=50, feature_limit=50)
                status = service.sync_status()

            self.assertEqual(result["schema_mode"], "legacy_betsignal")
            self.assertEqual(result["imported_matches"], 1)
            self.assertGreaterEqual(result["imported_features"], 1)
            self.assertGreaterEqual(result["imported_league_scores"], 1)
            self.assertEqual(status["schema_mode"], "legacy_betsignal")
            self.assertTrue(status["available_tables"]["betsignal_games"])
            self.assertFalse(status["available_tables"]["historical_matches"])

            snapshot = repo.system_snapshot()
            self.assertEqual(snapshot["counts"]["historical_matches"], 1)
            self.assertEqual(snapshot["counts"]["historical_features"], 1)
            self.assertEqual(snapshot["counts"]["league_reliability_scores"], 1)

            match = repo.list_historical_matches(limit=1)[0]
            self.assertEqual(match["source"], "Supabase Legacy")
            self.assertEqual(match["league"], "Brazilian Serie A")

    def test_hydrate_falls_back_to_legacy_when_historical_schema_breaks_mid_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FootballResearchRepository(f"{tmp}/research.db")
            service = ResearchSupabaseSyncService(
                repo,
                supabase_url="https://example.test",
                supabase_service_role_key="secret",
            )

            with patch(
                "services.footballQuantAiSkill.supabase.research_sync_service.httpx.Client",
                return_value=_HistoricalProbeBreaksButLegacyWorksClient(),
            ):
                result = service.hydrate_local_cache(match_limit=50, feature_limit=50)
                status = service.sync_status()

            self.assertEqual(result["schema_mode"], "legacy_betsignal")
            self.assertEqual(result["imported_matches"], 1)
            self.assertGreaterEqual(result["imported_features"], 1)
            self.assertEqual(status["schema_mode"], "historical")
            self.assertTrue(status["available_tables"]["historical_matches"])
            self.assertTrue(status["available_tables"]["betsignal_games"])

            snapshot = repo.system_snapshot()
            self.assertEqual(snapshot["counts"]["historical_matches"], 1)
            self.assertEqual(snapshot["counts"]["historical_features"], 1)

    def test_hydrate_skips_cleanly_when_no_remote_schema_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = FootballResearchRepository(f"{tmp}/research.db")
            service = ResearchSupabaseSyncService(
                repo,
                supabase_url="https://example.test",
                supabase_service_role_key="secret",
            )

            with patch("services.footballQuantAiSkill.supabase.research_sync_service.httpx.Client", return_value=_MissingSchemaClient()):
                result = service.hydrate_local_cache(match_limit=50, feature_limit=50)
                status = service.sync_status()

            self.assertTrue(result["skipped"])
            self.assertEqual(result["reason"], "remote_schema_unavailable")
            self.assertEqual(result["schema_mode"], "local_only")
            self.assertEqual(status["schema_mode"], "local_only")
            self.assertFalse(any(status["available_tables"].values()))


if __name__ == "__main__":
    unittest.main()
