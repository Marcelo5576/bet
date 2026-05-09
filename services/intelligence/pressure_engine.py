from __future__ import annotations

from typing import Any

from services.markets.pressure_index import live_pressure_payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def evaluate_pressure_engine(game: dict[str, Any]) -> dict[str, Any]:
    markets = game.get("markets") if isinstance(game.get("markets"), dict) else {}
    facts = markets.get("live_facts") if isinstance(markets.get("live_facts"), dict) else {}
    minute = max(1, _safe_int(game.get("minute")))

    stats_available = any(
        facts.get(key) is not None
        for key in (
            "shots_home",
            "shots_away",
            "shots_on_home",
            "shots_on_away",
            "corners_home",
            "corners_away",
            "dangerous_attacks_home",
            "dangerous_attacks_away",
            "possession_home",
            "possession_away",
        )
    ) or any(game.get(key) is not None for key in ("home_pressure", "away_pressure", "home_shots_on", "away_shots_on"))

    base = live_pressure_payload(game)
    home_pressure = _safe_float(base.get("home_pressure_index"))
    away_pressure = _safe_float(base.get("away_pressure_index"))
    combined_pressure = home_pressure + away_pressure
    dangerous_attacks_rate = (
        _safe_float(facts.get("dangerous_attacks_home"))
        + _safe_float(facts.get("dangerous_attacks_away"))
    ) / minute
    shots_momentum = (
        (_safe_float(facts.get("shots_on_home")) + (_safe_float(facts.get("shots_home")) * 0.5))
        - (_safe_float(facts.get("shots_on_away")) + (_safe_float(facts.get("shots_away")) * 0.5))
    )
    corners_momentum = _safe_float(facts.get("corners_home")) - _safe_float(facts.get("corners_away"))
    cards_risk = min(
        100.0,
        (
            (_safe_float(facts.get("yellow_home")) + _safe_float(facts.get("yellow_away"))) * 8.0
            + (_safe_float(facts.get("red_home")) + _safe_float(facts.get("red_away"))) * 18.0
            + (_safe_float(facts.get("fouls_home")) + _safe_float(facts.get("fouls_away"))) * 0.45
        ),
    )
    late_goal_pressure = min(100.0, max(0.0, combined_pressure * (1.2 if minute >= 65 else 0.7)))
    tempo_pressure = min(100.0, max(0.0, (combined_pressure / max(1.0, minute)) * 3.4))
    attacking_momentum = min(100.0, max(0.0, 50.0 + base.get("momentum_score", 0.0)))
    pressure_index = min(100.0, max(0.0, combined_pressure / 2.0))

    return {
        "status": "ok" if stats_available else "data_insufficient",
        "data_insufficient": not stats_available,
        "pressure_index": round(pressure_index, 1),
        "attacking_momentum": round(attacking_momentum, 1),
        "dangerous_attacks_rate": round(dangerous_attacks_rate, 3),
        "shots_momentum": round(shots_momentum, 2),
        "corners_momentum": round(corners_momentum, 2),
        "cards_risk": round(cards_risk, 1),
        "late_goal_pressure": round(late_goal_pressure, 1),
        "tempo_pressure": round(tempo_pressure, 1),
        "leader": base.get("leader"),
        "intensity": base.get("intensity"),
        "territorial_dominance": base.get("territorial_dominance") or {},
    }
