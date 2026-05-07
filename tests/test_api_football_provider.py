from __future__ import annotations

import unittest

import httpx

if not hasattr(httpx, "HTTPError"):
    httpx.HTTPError = Exception  # type: ignore[attr-defined]

from services.footballQuantAiSkill.data_sources.api_football_provider import (
    ApiFootballProvider,
    normalize_market_name,
    normalize_selection_name,
)
from src.cache import TTLCache
from src.intelligence.recommendation_policy import apply_recommendation_policy
from src.rate_limiter import ProviderRateLimiter


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: list[dict] | dict | None = None,
        *,
        headers: dict[str, str] | None = None,
        errors: dict[str, str] | list[str] | str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}
        self.errors = errors or []
        self.content = b"{}"

    def json(self) -> dict[str, object]:
        return {"response": self._payload, "errors": self.errors}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        self.calls.append((url, params, headers))
        if not self.responses:
            raise AssertionError("Sem resposta fake restante para a chamada.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ApiFootballProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_check_ok(self):
        client = FakeAsyncClient(
            [
                FakeResponse(
                    200,
                    [
                        {
                            "league": {"id": 71, "name": "Serie A", "type": "League", "logo": "x"},
                            "country": {"name": "Brazil", "code": "BR"},
                            "seasons": [{"year": 2026, "current": True}],
                        }
                    ],
                )
            ]
        )
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        result = await provider.health_check()

        self.assertTrue(result["ok"])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][2]["x-apisports-key"], "test-key")

    async def test_get_leagues_raises_on_401(self):
        client = FakeAsyncClient([FakeResponse(401, [])])
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        with self.assertRaises(RuntimeError):
            await provider.get_leagues()

    async def test_429_uses_stale_cache(self):
        cache = TTLCache()
        stale_key = "api-football:fixtures:live"
        stale_value = [
            {
                "fixture": {
                    "id": 9,
                    "date": "2026-05-04T20:00:00+00:00",
                    "status": {"elapsed": 55, "short": "2H", "long": "Second Half"},
                },
                "league": {"name": "Serie A", "country": "Brazil"},
                "teams": {"home": {"name": "Casa"}, "away": {"name": "Fora"}},
                "goals": {"home": 1, "away": 0},
                "score": {"halftime": {"home": 1, "away": 0}, "fulltime": {"home": None, "away": None}},
            }
        ]
        cache.set(stale_key, stale_value, 1, stale_seconds=120)
        entry = cache._entries[stale_key]  # type: ignore[attr-defined]
        entry.expires_at = entry.stored_at - 1

        client = FakeAsyncClient([FakeResponse(429, [], headers={"Retry-After": "15"}), FakeResponse(200, [])])
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=cache,
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        fixtures = await provider.get_live_fixtures()

        self.assertEqual(len(fixtures), 1)
        self.assertTrue(provider.status_snapshot()["fallback_active"])
        self.assertEqual(fixtures[0]["fixture_id"], "9")

    async def test_normalize_fixture(self):
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
        )
        normalized = provider.normalize_fixture(
            {
                "fixture": {
                    "id": 123,
                    "date": "2026-05-04T20:00:00+00:00",
                    "status": {"elapsed": 44, "short": "1H", "long": "First Half"},
                    "venue": {"name": "Arena"},
                },
                "league": {"name": "Serie A", "country": "Brazil"},
                "teams": {"home": {"name": "Botafogo"}, "away": {"name": "Bahia"}},
                "goals": {"home": 1, "away": 1},
                "score": {"halftime": {"home": 1, "away": 1}, "fulltime": {"home": None, "away": None}},
            },
            odds={"1x2": {"home": 2.1, "draw": 3.1, "away": 3.6}},
            stats={"home": {"pressure_index": 88, "shots_on": 4}, "away": {"pressure_index": 66, "shots_on": 2}},
        )

        self.assertEqual(normalized["fixture_id"], "123")
        self.assertEqual(normalized["home_team"], "Botafogo")
        self.assertEqual(normalized["away_team"], "Bahia")
        self.assertEqual(normalized["minute"], 44)
        self.assertEqual(normalized["odds_home"], 2.1)
        self.assertEqual(normalized["source"], "api-football")

    async def test_cache_avoids_repeated_calls(self):
        client = FakeAsyncClient([FakeResponse(200, [])])
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        await provider.get_leagues()
        await provider.get_leagues()

        self.assertEqual(len(client.calls), 1)

    def test_policy_blocks_entry_without_odds(self):
        signal = {
            "game": {"league": "Serie A", "minute": 51},
            "entry_market": "Over 2.5",
            "confidence": 71,
            "estimated_probability": 0.61,
            "data_quality": 92,
            "entry_score": 74,
            "action": "ENTRAR",
        }
        decided = apply_recommendation_policy(
            signal,
            {"roi_units": 4.0, "market_breakdown": [], "league_breakdown": []},
            bankroll=1000.0,
            unit_percent=1.0,
            selected_profile="moderado",
        )

        self.assertFalse(decided["entry_allowed"])
        self.assertEqual(decided["stake_value"], 0.0)
        self.assertIn("Odd invalida ou ausente", " ".join(decided["decision_reasons"]))

    def test_normalize_market_name(self):
        self.assertEqual(normalize_market_name("Match Winner"), "1X2")
        self.assertEqual(normalize_market_name("Goals Over/Under"), "OVER_UNDER")
        self.assertEqual(normalize_market_name("Both Teams Score"), "BTTS")
        self.assertEqual(normalize_market_name("Exact Score"), "UNSUPPORTED")

    def test_normalize_selection_name_with_team_names(self):
        self.assertEqual(normalize_selection_name("Botafogo", "Botafogo", "Bahia"), "home")
        self.assertEqual(normalize_selection_name("Bahia", "Botafogo", "Bahia"), "away")
        self.assertEqual(normalize_selection_name("Draw", "Botafogo", "Bahia"), "draw")
        self.assertEqual(normalize_selection_name("Over 2.5"), "over_2_5")
        self.assertEqual(normalize_selection_name("Yes"), "btts_yes")

    def test_normalize_odds_1x2_with_team_names(self):
        provider = ApiFootballProvider("test-key", "https://v3.football.api-sports.io", cache=TTLCache(), limiter=ProviderRateLimiter())
        odds = provider.normalize_odds(
            {
                "bookmakers": [
                    {
                        "name": "Book",
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Botafogo", "odd": "2.10"},
                                    {"value": "Draw", "odd": "3.15"},
                                    {"value": "Bahia", "odd": "3.80"},
                                ],
                            }
                        ],
                    }
                ]
            },
            home_team="Botafogo",
            away_team="Bahia",
        )

        self.assertEqual(odds["1x2"]["home"], 2.1)
        self.assertEqual(odds["1x2"]["draw"], 3.15)
        self.assertEqual(odds["1x2"]["away"], 3.8)
        self.assertTrue(odds["_meta"]["confirmed"])

    def test_normalize_odds_over_under_and_btts(self):
        provider = ApiFootballProvider("test-key", "https://v3.football.api-sports.io", cache=TTLCache(), limiter=ProviderRateLimiter())
        odds = provider.normalize_odds(
            {
                "bookmakers": [
                    {
                        "name": "Book",
                        "bets": [
                            {
                                "name": "Goals Over/Under",
                                "values": [
                                    {"value": "Over 2.5", "odd": "1.91"},
                                    {"value": "Under 2.5", "odd": "1.89"},
                                ],
                            },
                            {
                                "name": "Both Teams Score",
                                "values": [
                                    {"value": "Yes", "odd": "1.75"},
                                    {"value": "No", "odd": "2.05"},
                                ],
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(odds["goals"]["over"]["line"], "2.5")
        self.assertEqual(odds["goals"]["under"]["odds"], 1.89)
        self.assertEqual(odds["btts"]["yes"], 1.75)
        self.assertEqual(odds["btts"]["no"], 2.05)

    def test_unsupported_market_is_preserved_in_debug_metadata(self):
        provider = ApiFootballProvider("test-key", "https://v3.football.api-sports.io", cache=TTLCache(), limiter=ProviderRateLimiter())
        detailed = provider.normalize_odds_detailed(
            {
                "bookmakers": [
                    {
                        "name": "Book",
                        "bets": [
                            {
                                "name": "Exact Score",
                                "values": [{"value": "1:0", "odd": "7.00"}],
                            }
                        ],
                    }
                ]
            }
        )

        self.assertEqual(detailed["normalized_count"], 0)
        self.assertEqual(detailed["unsupported_markets"][0]["market"], "Exact Score")
        self.assertIn("unsupported_market", detailed["diagnosis"])

    async def test_empty_odds_returns_unavailable(self):
        client = FakeAsyncClient([FakeResponse(200, [])])
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        result = await provider.get_odds_by_fixture_or_fallback("123")

        self.assertTrue(result["odds_unavailable"])
        self.assertEqual(result["raw_count"], 0)
        self.assertIn("0 odds", result["reason"])

    async def test_odds_api_200_with_error_is_not_treated_as_success(self):
        client = FakeAsyncClient(
            [
                FakeResponse(
                    200,
                    [],
                    errors={"requests": "You have reached the request limit for the day."},
                )
            ]
        )
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        with self.assertRaises(RuntimeError):
            await provider.get_odds_by_fixture_or_fallback("123")

        self.assertIn("request limit", provider.status_snapshot()["last_error"])

    async def test_fixture_odds_debug_reports_403_plan(self):
        client = FakeAsyncClient([FakeResponse(403, [])])
        provider = ApiFootballProvider(
            "test-key",
            "https://v3.football.api-sports.io",
            cache=TTLCache(),
            limiter=ProviderRateLimiter(),
            client_factory=lambda timeout: client,
        )

        debug = await provider.fixture_odds_debug("123")

        self.assertEqual(debug["status"], 403)
        self.assertIn("plano", debug["diagnosis"])
        self.assertNotIn("test-key", debug["request_url"])

if __name__ == "__main__":
    unittest.main()
