from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings


def main() -> int:
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase não configurado. Rodando em modo local.")
        return 0

    auth_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/health"
    rest_url = f"{settings.supabase_url.rstrip('/')}/rest/v1/"
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    payload: dict[str, object] = {
        "configured": True,
        "supabase_url": settings.supabase_url,
        "auth_url": auth_url,
        "rest_url": rest_url,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            auth_response = client.get(auth_url)
            payload["auth_status"] = auth_response.status_code

            rest_response = client.get(rest_url, headers=headers)
            payload["rest_status"] = rest_response.status_code

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if auth_response.status_code >= 500 or rest_response.status_code in {401, 403}:
            print("Supabase configurado, mas a conexão falhou ou a chave não tem permissão suficiente.")
            return 1
        return 0
    except Exception as exc:
        payload["error"] = str(exc)[:200]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("Supabase configurado, mas não foi possível validar a conexão.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
