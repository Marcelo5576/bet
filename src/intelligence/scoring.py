from __future__ import annotations

from typing import Any


def entry_score(signal: dict[str, Any], learning_context: dict[str, Any]) -> dict[str, Any]:
    confidence = float(signal.get("confidence") or 0)
    data_quality = float(signal.get("data_quality") or 0)
    edge = signal.get("value_edge")
    game = signal.get("game") or {}
    minute = _safe_int(game.get("minute"))
    home_pressure = _safe_int(game.get("home_pressure"))
    away_pressure = _safe_int(game.get("away_pressure"))
    home_shots_on = _safe_int(game.get("home_shots_on"))
    away_shots_on = _safe_int(game.get("away_shots_on"))
    sample_size = int(learning_context.get("sample_size") or 0)
    brier = learning_context.get("brier_score")
    roi = float(learning_context.get("roi_units") or 0)

    score = 0
    score += min(35, confidence * 0.35)
    score += min(25, data_quality * 0.25)
    score += _edge_points(edge)
    score += _sample_points(sample_size)
    score += _roi_points(roi)
    score += _brier_points(brier)
    score += _fast_learning_points(signal, learning_context.get("fast_learning") or {})
    score += _live_pressure_points(home_pressure, away_pressure, home_shots_on, away_shots_on)
    score += _minute_points(minute)
    score += _price_points(signal)
    score += _red_card_points(signal)

    if signal.get("risk_blocked"):
        score = 0
    if signal.get("action") in {"SAIR", "SEGURAR"}:
        score = min(score, 45)
    if signal.get("action") == "AGUARDAR":
        score = min(score, 68)

    score = int(max(0, min(100, round(score))))
    return {
        "entry_score": score,
        "risk_score": 100 - score,
        "grade": _grade(score),
        "score_note": _note(score),
        "decision_class": _decision_class(signal, score),
    }


def _edge_points(edge) -> float:
    if edge is None:
        return 4
    edge = float(edge)
    if edge >= 0.10:
        return 22
    if edge >= 0.05:
        return 16
    if edge >= 0:
        return 9
    if edge >= -0.05:
        return 3
    return -8


def _sample_points(sample_size: int) -> float:
    if sample_size >= 100:
        return 10
    if sample_size >= 30:
        return 7
    if sample_size >= 10:
        return 4
    return 1


def _roi_points(roi: float) -> float:
    if roi >= 20:
        return 5
    if roi > 0:
        return 3
    if roi < -10:
        return -4
    return 0


def _brier_points(brier) -> float:
    if brier is None:
        return 1
    brier = float(brier)
    if brier <= 0.18:
        return 3
    if brier <= 0.25:
        return 1
    return -3


def _fast_learning_points(signal: dict[str, Any], fast: dict[str, Any]) -> float:
    if not fast:
        return 0
    points = 0.0
    mode = fast.get("mode")
    if mode == "defensivo":
        points -= 8
    elif mode == "oportunista":
        points += 3

    recent_5 = fast.get("recent_5") or {}
    recent_10 = fast.get("recent_10") or {}
    if int(recent_5.get("total") or 0) >= 4:
        hit_rate = float(recent_5.get("hit_rate") or 0)
        if hit_rate <= 25:
            points -= 8
        elif hit_rate <= 40:
            points -= 5
        elif hit_rate >= 65:
            points += 4
    elif int(recent_10.get("total") or 0) >= 7 and float(recent_10.get("hit_rate") or 0) < 40:
        points -= 4

    points += _group_adjustment(signal, fast.get("hot_markets") or [], 4)
    points += _group_adjustment(signal, fast.get("cold_markets") or [], -7)
    points += _group_adjustment(signal, fast.get("hot_teams") or [], 3)
    points += _group_adjustment(signal, fast.get("cold_teams") or [], -5)
    points += _group_adjustment(signal, fast.get("hot_leagues") or [], 2)
    points += _group_adjustment(signal, fast.get("cold_leagues") or [], -4)
    return points


def _group_adjustment(signal: dict[str, Any], rows: list[dict[str, Any]], value: float) -> float:
    match = _find_matching_group(signal, rows)
    if not match:
        return 0
    confidence = match.get("confidence")
    multiplier = 0.5 if confidence == "baixa" else 0.75 if confidence == "media" else 1
    return value * multiplier


def _find_matching_group(signal: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    haystack = _signal_terms(signal)
    for row in rows:
        name = str(row.get("name") or "").strip().lower()
        if name and (name in haystack or any(part and part in haystack for part in name.split(" x "))):
            return row
    return None


def _signal_terms(signal: dict[str, Any]) -> str:
    game = signal.get("game") or {}
    return " | ".join(
        str(value or "").lower()
        for value in (
            signal.get("entry_market"),
            signal.get("market"),
            signal.get("team"),
            game.get("home"),
            game.get("away"),
            game.get("league"),
            game.get("division"),
        )
    )


def _grade(score: int) -> str:
    if score >= 82:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "E"


def _note(score: int) -> str:
    if score >= 82:
        return "entrada forte, ainda exige confirmacao manual."
    if score >= 70:
        return "boa leitura, aguarde odd correta."
    if score >= 55:
        return "observavel, risco moderado."
    if score >= 40:
        return "fraco; melhor acompanhar sem entrar."
    return "evitar entrada."


def _minute_points(minute: int) -> float:
    if minute <= 0:
        return -8
    if minute < 15:
        return -8
    if minute <= 35:
        return 6
    if minute <= 45:
        return -3
    if minute <= 65:
        return 9
    if minute <= 75:
        return 2
    if minute < 80:
        return -6
    return -12


def _live_pressure_points(
    home_pressure: int,
    away_pressure: int,
    home_shots_on: int,
    away_shots_on: int,
) -> float:
    dominant_pressure = max(home_pressure, away_pressure)
    pressure_gap = abs(home_pressure - away_pressure)
    shots_total = home_shots_on + away_shots_on
    shots_gap = abs(home_shots_on - away_shots_on)
    points = 0.0
    points += min(10, dominant_pressure * 0.08)
    points += min(8, pressure_gap * 0.12)
    points += min(10, shots_total * 1.5)
    points += min(6, shots_gap * 1.25)
    return points


def _price_points(signal: dict[str, Any]) -> float:
    target_odds = _safe_float(signal.get("target_odds"))
    fair_odds = _safe_float(signal.get("fair_odds"))
    edge = _safe_float(signal.get("value_edge"))
    if target_odds is None or fair_odds is None:
        return -2
    if edge is not None and edge <= 0:
        return -12
    if target_odds <= fair_odds:
        return -10
    premium = (target_odds - fair_odds) / max(fair_odds, 1.0)
    if premium >= 0.12:
        return 8
    if premium >= 0.05:
        return 5
    return 1


def _red_card_points(signal: dict[str, Any]) -> float:
    brain = signal.get("brain") if isinstance(signal.get("brain"), dict) else {}
    facts = brain.get("facts") if isinstance(brain.get("facts"), dict) else {}
    reds = _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away"))
    if reds <= 0:
        return 0
    return -18


def _decision_class(signal: dict[str, Any], score: int) -> str:
    action = str(signal.get("action") or "").upper()
    edge = _safe_float(signal.get("value_edge")) or 0.0
    minute = _safe_int((signal.get("game") or {}).get("minute"))
    if action == "SAIR":
        return "SAI"
    if action == "SEGURAR":
        return "SEGURA"
    if action == "ENTRAR" and score >= 82 and edge >= 0.05 and minute <= 75:
        return "ENTRA_FORTE"
    if action == "ENTRAR":
        return "ENTRA_LEVE"
    if action == "AGUARDAR" and score >= 55:
        return "ESPERA"
    return "NO_BET"


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
