from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.dashboard import app


class PortalCanonicalDomainTests(unittest.TestCase):
    def test_landing_robots_and_sitemap_use_apexgol_domain(self) -> None:
        settings = SimpleNamespace(
            website_url="https://apexgol.com.br",
            product_name="ApexGol AI",
            product_tagline="Central Quantitativa",
            dashboard_domains=["https://apexgol.com.br", "https://www.apexgol.com.br"],
        )
        plans = {
            "starter": {"label": "Starter", "price": 97.0, "features": ["Scanner"]},
            "pro": {"label": "Pro", "price": 197.0, "features": ["IA"]},
            "team": {"label": "Team", "price": 497.0, "features": ["Multi"]},
        }

        with (
            patch("src.portal_web._settings", return_value=settings),
            patch("src.portal_web._portal_store", return_value=object()),
            patch("src.portal_web._plan_catalog", return_value=plans),
        ):
            client = TestClient(app)

            landing = client.get("/")
            robots = client.get("/robots.txt")
            sitemap = client.get("/sitemap.xml")

        self.assertEqual(landing.status_code, 200)
        self.assertIn('rel="canonical" href="https://apexgol.com.br/"', landing.text)
        self.assertIn("Sitemap: https://apexgol.com.br/sitemap.xml", robots.text)
        self.assertIn("<loc>https://apexgol.com.br/</loc>", sitemap.text)
        self.assertNotIn("novo.tickpost.com.br", landing.text)
        self.assertNotIn("novo.tickpost.com.br", robots.text)
        self.assertNotIn("novo.tickpost.com.br", sitemap.text)

    def test_request_host_can_override_stale_old_domain_config(self) -> None:
        settings = SimpleNamespace(
            website_url="https://novo.tickpost.com.br",
            product_name="ApexGol AI",
            product_tagline="Central Quantitativa",
            dashboard_domains=["https://novo.tickpost.com.br"],
        )
        plans = {
            "starter": {"label": "Starter", "price": 97.0, "features": ["Scanner"]},
            "pro": {"label": "Pro", "price": 197.0, "features": ["IA"]},
            "team": {"label": "Team", "price": 497.0, "features": ["Multi"]},
        }

        with (
            patch("src.portal_web._settings", return_value=settings),
            patch("src.portal_web._portal_store", return_value=object()),
            patch("src.portal_web._plan_catalog", return_value=plans),
        ):
            client = TestClient(app)
            landing = client.get("/", headers={"host": "apexgol.com.br"})
            robots = client.get("/robots.txt", headers={"host": "apexgol.com.br"})
            sitemap = client.get("/sitemap.xml", headers={"host": "apexgol.com.br"})

        self.assertIn('rel="canonical" href="https://apexgol.com.br/"', landing.text)
        self.assertIn("Sitemap: https://apexgol.com.br/sitemap.xml", robots.text)
        self.assertIn("<loc>https://apexgol.com.br/</loc>", sitemap.text)


if __name__ == "__main__":
    unittest.main()
