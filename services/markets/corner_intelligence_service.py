from __future__ import annotations

from typing import Any

from .market_normalizer import normalize_internal_markets


def evaluate_corners_market(game: dict[str, Any]) -> dict[str, Any]:
    offers = [
        item
        for item in normalize_internal_markets(game)
        if item.get("market_group") == "corners" and item.get("selection") == "over"
    ]
    confirmed = [item for item in offers if item.get("is_confirmed")]
    facts = _facts(game)
    minute = _safe_int(game.get("minute"))
    corners_home = _safe_int(facts.get("corners_home"))
    corners_away = _safe_int(facts.get("corners_away"))
    corners_total = corners_home + corners_away
    dangerous = _safe_int(facts.get("dangerous_attacks_home")) + _safe_int(facts.get("dangerous_attacks_away"))
    pressure = _safe_int(game.get("home_pressure")) + _safe_int(game.get("away_pressure"))
    shots = _safe_int(facts.get("shots_home")) + _safe_int(facts.get("shots_away"))
    probability = min(0.86, 0.26 + corners_total * 0.035 + dangerous * 0.004 + pressure * 0.001 + shots * 0.012)
    late_probability = min(0.88, probability + (0.08 if minute >= 70 and pressure >= 110 else 0.0))
    best = _best_offer(confirmed)
    confirmed_odd = _safe_float(best.get("odd")) if best else None
    action = "MONITORAR"
    block = []
    if not best:
        block.append("Sem odd real confirmada para escanteios.")
    if minute < 15:
        block.append("Minuto ainda cedo para mercado live de escanteios.")
    if pressure < 80 and dangerous < 12:
        block.append("Pressao lateral ainda insuficiente.")
    if best and minute >= 15 and probability >= 0.64 and pressure >= 95:
        action = "ENTRAR"
    return {
        "market": "Escanteios",
        "market_type": str(best.get("market_type") if best else "corners_total"),
        "selection": "Over escanteios",
        "line": best.get("line") if best else None,
        "odds": confirmed_odd,
        "action": action if best else "MONITORAR",
        "confirmed": bool(best),
        "confidence": round(probability * 100, 1),
        "late_corner_probability": round(late_probability * 100, 1),
        "pressure_corners": round((pressure * 0.45) + (dangerous * 0.6) + (corners_total * 7), 1),
        "reason": (
            "Mercado de escanteios favorável por pressão ofensiva contínua e volume lateral."
            if action == "ENTRAR" and best
            else "Monitorar escanteios: ainda falta confirmação de pressão, minuto ou odd."
        ),
        "blocking_reasons": block,
    }


def _best_offer(offers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not offers:
        return None
    return sorted(offers, key=lambda item: (_safe_float(item.get("odd")), item.get("period") == "FT"), reverse=True)[0]


def _facts(game: dict[str, Any]) -> dict[str, Any]:
    markets = game.get("markets") if isinstance(game.get("markets"), dict) else {}
    return markets.get("live_facts") if isinstance(markets.get("live_facts"), dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
