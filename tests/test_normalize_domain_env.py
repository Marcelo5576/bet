from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.normalize_domain_env import normalize_env_file


class NormalizeDomainEnvTests(unittest.TestCase):
    def test_normalize_env_file_rewrites_domain_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "APP_URL=https://novo.tickpost.com.br",
                        "WEBSITE_URL=https://novo.tickpost.com.br",
                        "DASHBOARD_DOMAINS=http://2.24.217.214,https://novo.tickpost.com.br",
                        "ADMIN_EMAIL=admin@apexgol.local",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            values = normalize_env_file(env_path, "apexgol.com.br")
            content = env_path.read_text(encoding="utf-8")

            self.assertEqual(values["APP_URL"], "https://apexgol.com.br")
            self.assertIn("APP_URL=https://apexgol.com.br", content)
            self.assertIn("WEBSITE_URL=https://apexgol.com.br", content)
            self.assertIn(
                "DASHBOARD_DOMAINS=http://2.24.217.214,https://apexgol.com.br,https://www.apexgol.com.br",
                content,
            )
            self.assertIn("ADMIN_EMAIL=admin@apexgol.local", content)


if __name__ == "__main__":
    unittest.main()
