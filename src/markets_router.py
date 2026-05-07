from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from services.markets import build_market_intelligence, supported_quant_markets
from services.markets.market_normalizer import normalize_internal_markets, normalize_market_name, normalize_selection_name
from src.config import load_settings
from src.portal_web import _require_user
from src.storage import StateStore


router = APIRouter(prefix="/api/markets", tags=["markets"])


@router.get("/supported")
def api_supported_markets(_: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "markets": supported_quant_markets(),
            "entry_policy": "Entradas só podem ser liberadas com odd real confirmada e mercado completo.",
        }
    )


@router.get("/normalize/debug")
def api_market_normalize_debug(
    market: str = "",
    selection: str = "",
    home: str = "",
    away: str = "",
    _: dict[str, Any] = Depends(_require_user),
) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "market_type": normalize_market_name(market),
            "selection": normalize_selection_name(selection, home, away),
        }
    )


@router.get("/live-summary")
def api_live_market_summary(_: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    settings = load_settings()
    state = StateStore(settings.state_file).read()
    live_games = [item for item in (state.live_games or []) if isinstance(item, dict)]
    summaries = [build_market_intelligence(game) for game in live_games[:30]]
    totals = {
        "games": len(live_games),
        "confirmed_offers": sum(int(item.get("confirmed_offers") or 0) for item in summaries),
        "corners_candidates": _count_action(summaries, "corners_intelligence"),
        "cards_candidates": _count_action(summaries, "referee_intelligence"),
        "asian_candidates": _count_action(summaries, "asian_market_intelligence"),
        "htst_candidates": _count_action(summaries, "first_half_market_engine")
        + _count_action(summaries, "second_half_market_engine"),
    }
    return JSONResponse(
        {
            "ok": True,
            "updated_from": settings.state_file,
            "totals": totals,
            "games": summaries,
            "policy": "Sem odd real confirmada, o mercado fica apenas em monitoramento.",
        }
    )


@router.post("/normalize/internal")
def api_normalize_internal_market(payload: dict[str, Any], _: dict[str, Any] = Depends(_require_user)) -> JSONResponse:
    return JSONResponse({"ok": True, "offers": normalize_internal_markets(payload)})


def _count_action(summaries: list[dict[str, Any]], module: str) -> int:
    total = 0
    for item in summaries:
        modules = item.get("modules") if isinstance(item.get("modules"), dict) else {}
        row = modules.get(module) if isinstance(modules.get(module), dict) else {}
        if row.get("confirmed") or str(row.get("action") or "").upper() == "ENTRAR":
            total += 1
    return total
