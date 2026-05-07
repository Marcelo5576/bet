from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai_brain_router import router as ai_brain_router  # noqa: F401
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


def main() -> None:
    app.dependency_overrides[_auth] = lambda: None
    app.dependency_overrides[_require_admin] = lambda: ADMIN_USER
    app.dependency_overrides[_require_user] = lambda: ADMIN_USER
    client = TestClient(app)

    try:
        for path in ["/", "/login", "/dashboard", "/cerebro-ia", "/api/system/rate-limit-protection"]:
            response = client.get(path, follow_redirects=False)
            print(f"{path} {response.status_code}")
            assert response.status_code in (200, 303, 307), response.text[:400]

        landing = client.get("/").text
        dashboard = client.get("/dashboard").text
        brain = client.get("/cerebro-ia").text
        rate_limit = client.get("/api/system/rate-limit-protection").json()

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

        assert "Scanner Ao Vivo" in dashboard
        assert "Cérebro IA" in brain or "Cerebro IA" in brain
        assert rate_limit.get("ok") is True
        assert "rate_limit_protection" in rate_limit

        print("smoke-install-ok")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
