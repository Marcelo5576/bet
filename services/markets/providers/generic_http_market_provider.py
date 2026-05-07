from __future__ import annotations

import os
from typing import Any

import httpx

from .base import ProviderMarketPayload


class ConfiguredHttpMarketProvider:
    """Provider HTTP plugável para mercados especializados.

    A classe só busca dados reais quando há chave e URL configuradas. Ela não
    cria mercado mockado e retorna erro controlado para o scanner continuar
    usando cache/fallback honesto.
    """

    name = "configured_http"

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key_env: str,
        endpoint: str,
        header_name: str = "Authorization",
        query_key_name: str | None = None,
        timeout_seconds: float = 6.0,
    ):
        self.name = name
        self.base_url = str(base_url or "").rstrip("/")
        self.api_key_env = api_key_env
        self.endpoint = endpoint
        self.header_name = header_name
        self.query_key_name = query_key_name
        self.timeout_seconds = timeout_seconds

    async def get_live_markets(self, event_id: str | None = None) -> ProviderMarketPayload:
        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            return ProviderMarketPayload(provider=self.name, event_id=str(event_id or ""), error=f"{self.api_key_env} ausente.")
        if not self.base_url or not self.endpoint:
            return ProviderMarketPayload(provider=self.name, event_id=str(event_id or ""), error="Provider sem endpoint configurado.")

        endpoint = self.endpoint.format(event_id=event_id or "")
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers: dict[str, str] = {}
        params: dict[str, Any] = {}
        if self.query_key_name:
            params[self.query_key_name] = api_key
        else:
            headers[self.header_name] = api_key if self.header_name.lower().startswith("x-") else f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url, headers=headers, params=params)
            if response.status_code == 429:
                return ProviderMarketPayload(provider=self.name, event_id=str(event_id or ""), error="429 Too Many Requests.")
            if response.status_code >= 400:
                return ProviderMarketPayload(provider=self.name, event_id=str(event_id or ""), error=f"HTTP {response.status_code}.")
            raw = response.json()
        except Exception as exc:
            return ProviderMarketPayload(provider=self.name, event_id=str(event_id or ""), error=str(exc)[:160])
        return ProviderMarketPayload(
            provider=self.name,
            event_id=str(event_id or ""),
            markets=_extract_market_rows(raw),
            raw_payload=raw if isinstance(raw, dict) else {"response": raw},
            is_real=True,
        )


def _extract_market_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("markets", "odds", "bookmakers", "response", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []
