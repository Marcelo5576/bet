from __future__ import annotations

from typing import Any


def entry_score(signal: dict[str, Any], learning_context: dict[str, Any]) -> dict[str, Any]:
    confidence = float(signal.get("confidence") or 0)
    data_quality = float(signal.get("data_quality") or 0)
    edge = signal.get("value_edge")
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
