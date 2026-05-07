from __future__ import annotations

import base64
import sys
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai_brain_router import router as ai_brain_router  # noqa: F401
from src.config import load_settings
from src.dashboard import _auth, app
from src.portal_web import _require_admin, _require_user


ADMIN_USER = {
    "id": 1,
    "email": "admin@local",
    "name": "Administrador",
    "is_admin": 1,
    "plan": "team",
    "status": "active",
}


def _basic_headers() -> dict[str, str]:
    settings = load_settings()
    raw = f"{settings.dashboard_user}:{settings.dashboard_password}".encode("utf-8")
    return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}


def main() -> None:
    app.dependency_overrides[_auth] = lambda: None
    app.dependency_overrides[_require_admin] = lambda: ADMIN_USER
    app.dependency_overrides[_require_user] = lambda: ADMIN_USER
    client = TestClient(app)
    auth_headers = _basic_headers()

    try:
        public_paths = ["/", "/login"]
        protected_paths = [
            "/dashboard",
            "/cerebro-ia",
            "/api/system/rate-limit-protection",
            "/api/ai-brain/metrics",
            "/api/ai-brain/summary",
        ]

        for path in public_paths:
            response = client.get(path, follow_redirects=False)
            print(f"{path} {response.status_code}")
            assert response.status_code in (200, 303, 307), response.text[:400]

        for path in protected_paths:
            response = client.get(path, headers=auth_headers, follow_redirects=False)
            print(f"{path} {response.status_code}")
            assert response.status_code in (200, 303, 307), response.text[:400]

        landing = client.get("/").text
        dashboard = client.get("/dashboard", headers=auth_headers, follow_redirects=True).text
        rate_limit = client.get("/api/system/rate-limit-protection", headers=auth_headers).json()
        brain_metrics = client.get("/api/ai-brain/metrics", headers=auth_headers).json()
        brain_summary = client.get("/api/ai-brain/summary", headers=auth_headers).json()

        for module_name in ["src.dashboard", "src.portal_web", "src.ai_brain_router"]:
            __import__(module_name)

        for required in [
            "APEXGOL AI",
            "Central Quantitativa de Inteligência Esportiva",
            "Scanner IA",
            "Cérebro IA",
            "Telegram Analyst",
            "Testar grátis por 7 dias",
        ]:
            assert required in landing, f"missing landing text: {required}"

        for legacy in [
            "Fantasy",
            "Jogos do Dia",
            "Live Center",
            "ENTRA_FORTE",
            "Decision class",
        ]:
            assert legacy not in landing, f"legacy landing text still visible: {legacy}"

        assert "Scanner Ao Vivo" in dashboard or "ApexGol AI" in dashboard
        assert rate_limit.get("ok") is True
        assert "rate_limit_protection" in rate_limit
        assert "status" in brain_metrics
        assert "ia_maturity_score" in brain_metrics
        assert "summary" in brain_summary

        print("smoke-install-ok")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
