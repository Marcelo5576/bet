from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

import httpx

from src.providers.espn import EspnProvider


class EspnProviderTests(unittest.TestCase):
    def test_provider_builds_urls_from_site_api_base_url(self) -> None:
        provider = EspnProvider(
            site_api_base_url="https://site.example.test",
            leagues=("bra.1", "eng.1"),
        )

        self.assertEqual(
            provider.urls[0],
            "https://site.example.test/apis/site/v2/sports/soccer/all/scoreboard",
        )
        self.assertIn(
            "https://site.example.test/apis/site/v2/sports/soccer/bra.1/scoreboard",
            provider.urls,
        )
        self.assertIn(
            "https://site.example.test/apis/site/v2/sports/soccer/eng.1/scoreboard",
            provider.urls,
        )

    def test_fetch_scoreboard_retries_until_success(self) -> None:
        provider = EspnProvider(max_retries=3, timeout_seconds=30, user_agent="ApexGol-ESPN/2.0")
        request = httpx.Request("GET", provider.urls[0])
        response = httpx.Response(
            200,
            json={"events": [], "leagues": []},
            request=request,
        )
        client = Mock()
        client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("temporary", request=request),
                response,
            ]
        )

        result = asyncio.run(provider._fetch_scoreboard(client, provider.urls[0]))

        self.assertEqual(result, {"events": [], "leagues": []})
        self.assertEqual(client.get.await_count, 2)


if __name__ == "__main__":
    unittest.main()
