from __future__ import annotations

from typing import Any


def pressure_index(
    *,
    shots_on_target: Any = 0,
    shots: Any = 0,
    dangerous_attacks: Any = 0,
    corners: Any = 0,
    possession: Any = 50,
) -> float:
    return round(
        _num(shots_on_target) * 2.5
        + _num(shots) * 1.2
        + _num(dangerous_attacks) * 0.8
        + _num(corners) * 1.0
        + _num(possession) * 0.15,
        2,
    )


def live_pressure_payload(game: dict[str, Any]) -> dict[str, Any]:
    facts = _facts(game)
    home = pressure_index(
        shots_on_target=facts.get("shots_on_home") or game.get("home_shots_on"),
        shots=facts.get("shots_home"),
        dangerous_attacks=facts.get("dangerous_attacks_home") or _num(game.get("home_pressure")) * 0.55,
        corners=facts.get("corners_home"),
        possession=facts.get("possession_home") or 50,
    )
    away = pressure_index(
        shots_on_target=facts.get("shots_on_away") or game.get("away_shots_on"),
        shots=facts.get("shots_away"),
        dangerous_attacks=facts.get("dangerous_attacks_away") or _num(game.get("away_pressure")) * 0.55,
        corners=facts.get("corners_away"),
        possession=facts.get("possession_away") or 50,
    )
    momentum = round(home - away, 2)
    total = max(1.0, home + away)
    return {
        "home_pressure_index": home,
        "away_pressure_index": away,
        "momentum_score": momentum,
        "territorial_dominance": {
            "home_pct": round((home / total) * 100, 1),
            "away_pct": round((away / total) * 100, 1),
        },
        "leader": str(game.get("home") if momentum >= 0 else game.get("away") or "-"),
        "intensity": _intensity(home + away),
    }


def _intensity(total_pressure: float) -> str:
    if total_pressure >= 145:
        return "alta"
    if total_pressure >= 95:
        return "media"
    return "baixa"


def _facts(game: dict[str, Any]) -> dict[str, Any]:
    markets = game.get("markets") if isinstance(game.get("markets"), dict) else {}
    facts = markets.get("live_facts") if isinstance(markets.get("live_facts"), dict) else {}
    return facts


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value if value is not None else default).replace(",", "."))
    except (TypeError, ValueError):
        return default
