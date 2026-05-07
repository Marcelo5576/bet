from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .asian_market_service import evaluate_asian_market
from .corner_intelligence_service import evaluate_corners_market
from .market_normalizer import normalize_internal_markets, supported_markets_payload
from .pressure_index import live_pressure_payload
from .referee_analysis_service import evaluate_cards_market
from .time_market_engine import first_half_market_engine, second_half_market_engine


def supported_quant_markets() -> list[dict[str, Any]]:
    return supported_markets_payload()


def build_market_intelligence(
    game: dict[str, Any],
    *,
    provider: str = "internal",
    previous_offers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    offers = normalize_internal_markets(game, provider=provider)
    pressure = live_pressure_payload(game)
    corners = evaluate_corners_market(game)
    cards = evaluate_cards_market(game)
    asian = evaluate_asian_market(game, previous_offers=previous_offers)
    first_half = first_half_market_engine(game)
    second_half = second_half_market_engine(game)
    recommendations = _recommendations_from_modules([corners, cards, asian, first_half, second_half])
    confirmed_count = sum(1 for item in offers if item.get("is_confirmed"))
    unsupported = [item for item in offers if item.get("market_group") == "unsupported"]
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "supported_markets": supported_quant_markets(),
        "offers": offers,
        "confirmed_offers": confirmed_count,
        "unsupported_offers": unsupported,
        "pressure": pressure,
        "modules": {
            "pressure_engine": {
                "status": "ativo",
                "momentum_score": pressure.get("momentum_score"),
                "intensity": pressure.get("intensity"),
            },
            "momentum_engine": {
                "status": "ativo",
                "leader": pressure.get("leader"),
                "territorial_dominance": pressure.get("territorial_dominance"),
            },
            "corners_intelligence": corners,
            "referee_intelligence": cards,
            "asian_market_intelligence": asian,
            "first_half_market_engine": first_half,
            "second_half_market_engine": second_half,
        },
        "recommendations": recommendations,
        "safety": {
            "entry_requires_confirmed_odd": True,
            "confirmed_markets_only": True,
            "no_mock_odds": True,
            "blocked_without_odd": [
                item.get("market")
                for item in (corners, cards, asian, first_half, second_half)
                if not item.get("confirmed")
            ],
        },
    }


def _recommendations_from_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        best_offer = module.get("best_offer") if isinstance(module.get("best_offer"), dict) else {}
        market = str(module.get("market") or best_offer.get("market_type") or "-")
        action = str(module.get("action") or "MONITORAR").upper()
        confirmed = bool(module.get("confirmed"))
        if action == "ENTRAR" and not confirmed:
            action = "MONITORAR"
        rows.append(
            {
                "market": market,
                "selection": str(module.get("selection") or best_offer.get("selection") or "-"),
                "line": str(module.get("line") or best_offer.get("line") or "-"),
                "odds": module.get("odds") if module.get("odds") is not None else best_offer.get("odd"),
                "action": action,
                "confidence": module.get("confidence"),
                "entry": f"{market} · {module.get('selection') or best_offer.get('selection') or '-'}",
                "reason": str(module.get("reason") or ""),
                "confirmed": confirmed,
                "blocking_reasons": module.get("blocking_reasons") or ([] if confirmed else ["Sem odd real confirmada."]),
            }
        )
    rows.sort(
        key=lambda item: (
            item.get("action") == "ENTRAR",
            bool(item.get("confirmed")),
            _safe_float(item.get("confidence")),
            _safe_float(item.get("odds")),
        ),
        reverse=True,
    )
    return rows


def _safe_float(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
