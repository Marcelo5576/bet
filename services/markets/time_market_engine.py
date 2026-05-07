from __future__ import annotations

from typing import Any

from .market_normalizer import normalize_internal_markets


def first_half_market_engine(game: dict[str, Any]) -> dict[str, Any]:
    return _period_engine(game, period="HT")


def second_half_market_engine(game: dict[str, Any]) -> dict[str, Any]:
    return _period_engine(game, period="ST")


def _period_engine(game: dict[str, Any], *, period: str) -> dict[str, Any]:
    minute = _safe_int(game.get("minute"))
    if period == "HT":
        active_window = 12 <= minute <= 41
        title = "Mercados 1T"
    else:
        active_window = 48 <= minute <= 78
        title = "Mercados 2T"
    offers = [
        item
        for item in normalize_internal_markets(game)
        if item.get("period") == period and item.get("is_confirmed")
    ]
    facts = _facts(game)
    rhythm = (
        _safe_int(game.get("home_shots_on"))
        + _safe_int(game.get("away_shots_on"))
        + (_safe_int(facts.get("corners_home")) + _safe_int(facts.get("corners_away"))) * 0.7
        + (_safe_int(facts.get("dangerous_attacks_home")) + _safe_int(facts.get("dangerous_attacks_away"))) * 0.08
    )
    confidence = min(86.0, 35 + rhythm * 6 + (8 if active_window else 0))
    action = "MONITORAR"
    if offers and active_window and confidence >= 66:
        action = "ENTRAR"
    return {
        "market": title,
        "period": period,
        "available_offers": len(offers),
        "action": action if offers else "MONITORAR",
        "confirmed": bool(offers),
        "confidence": round(confidence, 1),
        "rhythm_index": round(rhythm, 1),
        "best_offer": sorted(offers, key=lambda item: _safe_float(item.get("odd"), 0.0), reverse=True)[0] if offers else None,
        "reason": (
            f"{title} com linha confirmada e ritmo acima do corte operacional."
            if offers and action == "ENTRAR"
            else f"{title} em monitoramento: precisa linha confirmada e janela/minuto adequado."
        ),
    }


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
