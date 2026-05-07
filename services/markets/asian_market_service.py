from __future__ import annotations

from typing import Any

from .market_normalizer import normalize_internal_markets


def evaluate_asian_market(game: dict[str, Any], previous_offers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    offers = [
        item
        for item in normalize_internal_markets(game)
        if item.get("market_group") == "asian" and item.get("market_type") == "asian_handicap"
    ]
    confirmed = [item for item in offers if item.get("is_confirmed")]
    best = _best_side_offer(game, confirmed)
    drift = _line_drift(best, previous_offers or [])
    steam = abs(drift.get("line_delta") or 0.0) >= 0.25 or abs(drift.get("odd_delta") or 0.0) >= 0.12
    pressure_side = _pressure_side(game)
    sharp_side = best.get("side") if best and best.get("side") == pressure_side else None
    confidence = 46.0
    if best:
        confidence += 12
    if sharp_side:
        confidence += 16
    if steam:
        confidence += 8
    confidence = min(88.0, confidence)
    action = "MONITORAR"
    block = []
    if not best:
        block.append("Sem linha asiática real confirmada.")
    if not sharp_side:
        block.append("Linha não está alinhada ao lado de maior pressão.")
    if best and sharp_side and confidence >= 68:
        action = "ENTRAR"
    return {
        "market": "Asian Handicap",
        "market_type": "asian_handicap",
        "selection": _selection_label(game, str(best.get("side") if best else pressure_side or "")),
        "line": best.get("line") if best else None,
        "odds": _safe_float(best.get("odd"), None) if best else None,
        "action": action if best else "MONITORAR",
        "confirmed": bool(best),
        "confidence": round(confidence, 1),
        "line_drift": drift,
        "steam_detected": steam,
        "sharp_side": sharp_side,
        "closing_line_value": drift.get("clv_estimate"),
        "reason": (
            "Handicap asiático alinhado ao lado de pressão, com linha confirmada."
            if action == "ENTRAR" and best
            else "Monitorar asiático: precisa linha real, liquidez e lado dominante mais claro."
        ),
        "blocking_reasons": block,
    }


def _best_side_offer(game: dict[str, Any], offers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not offers:
        return None
    preferred = _pressure_side(game)
    ranked = sorted(
        offers,
        key=lambda item: (
            item.get("side") == preferred,
            _safe_float(item.get("odd"), 0.0),
        ),
        reverse=True,
    )
    return ranked[0]


def _pressure_side(game: dict[str, Any]) -> str | None:
    home = _safe_float(game.get("home_pressure"), 0.0) + _safe_float(game.get("home_shots_on"), 0.0) * 8
    away = _safe_float(game.get("away_pressure"), 0.0) + _safe_float(game.get("away_shots_on"), 0.0) * 8
    if abs(home - away) < 12:
        return None
    return "home" if home > away else "away"


def _line_drift(current: dict[str, Any] | None, previous: list[dict[str, Any]]) -> dict[str, Any]:
    if not current or not previous:
        return {"line_delta": 0.0, "odd_delta": 0.0, "clv_estimate": None}
    same = [
        item
        for item in previous
        if item.get("market_type") == current.get("market_type") and item.get("selection") == current.get("selection")
    ]
    if not same:
        return {"line_delta": 0.0, "odd_delta": 0.0, "clv_estimate": None}
    old = same[-1]
    line_delta = _safe_float(current.get("line"), 0.0) - _safe_float(old.get("line"), 0.0)
    odd_delta = _safe_float(current.get("odd"), 0.0) - _safe_float(old.get("odd"), 0.0)
    return {
        "line_delta": round(line_delta, 3),
        "odd_delta": round(odd_delta, 3),
        "clv_estimate": round(-line_delta + odd_delta * 0.2, 3),
    }


def _selection_label(game: dict[str, Any], side: str) -> str:
    if side == "home":
        return str(game.get("home") or "Casa")
    if side == "away":
        return str(game.get("away") or "Fora")
    return "Lado indefinido"


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default
