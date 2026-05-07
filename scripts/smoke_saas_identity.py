from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard import _auth, app
from src.portal_web import _require_admin, _require_user


ADMIN_USER = {
    "id": 1,
    "email": "admin@local",
    "name": "Admin",
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
        for path in ["/", "/login", "/app", "/admin/users", "/api/admin/telegram-approved-signals"]:
            response = client.get(path, follow_redirects=False)
            print(f"{path} {response.status_code}")
            assert response.status_code in (200, 303), response.text[:300]

        landing = client.get("/").text
        for required in [
            "APEXGOL AI",
            "Central Quantitativa de Inteligência Esportiva",
            "Scanner IA",
            "Cérebro IA",
            "Telegram Analyst",
            "Testar grátis por 7 dias",
            "Ver Scanner em ação",
            "O ApexGol AI é uma ferramenta estatística de apoio",
        ]:
            assert required in landing, f"missing landing text: {required}"

        for legacy in [
            "Fantasy Campeão",
            "Jogos do Dia",
            "Live Center",
            "ENTRA_FORTE",
            "Decision class",
            "Acessar sistema",
            "Quanto custa entrar sem leitura",
            "Backtests∞",
            "Tickpost CRM",
        ]:
            assert legacy not in landing, f"legacy landing text still visible: {legacy}"

        admin = client.get("/admin/users").text
        for required in [
            "Admin APEXGOL AI",
            "Telegram de entradas aprovadas",
            "Enviar apenas entradas aprovadas",
        ]:
            assert required in admin, f"missing admin text: {required}"

        legacy_page = client.get("/fantasy-ia", follow_redirects=False)
        print(f"/fantasy-ia {legacy_page.status_code} {legacy_page.headers.get('location')}")
        assert legacy_page.status_code == 303
        assert legacy_page.headers.get("location") == "/dashboard"

        telegram_api = client.get("/api/admin/telegram-approved-signals")
        assert telegram_api.status_code == 200
        payload = telegram_api.json()
        assert payload["ok"] is True
        assert payload["policy"].startswith("Somente sinais")

        print("saas-identity-smoke-ok")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
