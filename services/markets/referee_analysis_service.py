from __future__ import annotations

from typing import Any

from .market_normalizer import normalize_internal_markets


def evaluate_cards_market(game: dict[str, Any], referee_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = referee_profile or {}
    offers = [
        item
        for item in normalize_internal_markets(game)
        if item.get("market_group") == "cards" and item.get("selection") == "over"
    ]
    confirmed = [item for item in offers if item.get("is_confirmed")]
    best = _best_offer(confirmed)
    facts = _facts(game)
    minute = _safe_int(game.get("minute"))
    yellow_total = _safe_int(facts.get("yellow_home")) + _safe_int(facts.get("yellow_away"))
    red_total = _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away"))
    pressure_gap = abs(_safe_int(game.get("home_pressure")) - _safe_int(game.get("away_pressure")))
    referee_avg = _safe_float(profile.get("cards_avg"), 0.0)
    aggression_index = min(100.0, yellow_total * 14 + red_total * 28 + pressure_gap * 0.45 + referee_avg * 8)
    probability = min(0.88, 0.22 + yellow_total * 0.075 + red_total * 0.12 + pressure_gap * 0.003 + referee_avg * 0.025)
    action = "MONITORAR"
    block = []
    if not best:
        block.append("Sem odd real confirmada para cartões.")
    if minute < 20:
        block.append("Amostra de faltas/cartões ainda curta.")
    if aggression_index < 45:
        block.append("Índice de agressividade abaixo do mínimo.")
    if best and minute >= 20 and probability >= 0.63 and aggression_index >= 52:
        action = "ENTRAR"
    return {
        "market": "Cartões",
        "market_type": str(best.get("market_type") if best else "cards_total"),
        "selection": "Over cartões",
        "line": best.get("line") if best else None,
        "odds": _safe_float(best.get("odd"), None) if best else None,
        "action": action if best else "MONITORAR",
        "confirmed": bool(best),
        "confidence": round(probability * 100, 1),
        "aggression_index": round(aggression_index, 1),
        "referee_cards_avg": referee_avg if referee_avg > 0 else None,
        "reason": (
            "Mercado de cartões favorável por agressividade, árbitro/linha e pressão competitiva."
            if action == "ENTRAR" and best
            else "Monitorar cartões: falta odd confirmada, agressividade ou janela mínima."
        ),
        "blocking_reasons": block,
    }


def _best_offer(offers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not offers:
        return None
    return sorted(offers, key=lambda item: _safe_float(item.get("odd"), 0.0), reverse=True)[0]


def _facts(game: dict[str, Any]) -> dict[str, Any]:
    markets = game.get("markets") if isinstance(game.get("markets"), dict) else {}
    return markets.get("live_facts") if isinstance(markets.get("live_facts"), dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default
