from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskProfile:
    name: str
    label: str
    min_ev: float
    min_confidence_score: float
    odd_min: float
    odd_max: float
    stake_cap_fraction: float
    kelly_multiplier: float


PROFILES: dict[str, RiskProfile] = {
    "conservador": RiskProfile(
        name="conservador",
        label="Modo conservador",
        min_ev=0.07,
        min_confidence_score=0.72,
        odd_min=1.55,
        odd_max=2.70,
        stake_cap_fraction=0.01,
        kelly_multiplier=0.25,
    ),
    "moderado": RiskProfile(
        name="moderado",
        label="Modo moderado",
        min_ev=0.03,
        min_confidence_score=0.60,
        odd_min=1.45,
        odd_max=3.50,
        stake_cap_fraction=0.03,
        kelly_multiplier=0.50,
    ),
    "agressivo": RiskProfile(
        name="agressivo",
        label="Modo agressivo",
        min_ev=0.015,
        min_confidence_score=0.52,
        odd_min=1.35,
        odd_max=4.25,
        stake_cap_fraction=0.03,
        kelly_multiplier=0.75,
    ),
}


def resolve_profile(
    selected: str | None,
    learning_context: dict[str, Any] | None,
) -> tuple[RiskProfile, dict[str, Any]]:
    requested = str(selected or "moderado").strip().lower()
    if requested not in PROFILES:
        requested = "moderado"
    base = PROFILES[requested]
    roi = _safe_float((learning_context or {}).get("roi_units"))
    auto_conservative = roi < -10.0
    effective = PROFILES["conservador"] if auto_conservative else base
    return effective, {
        "selected_profile": requested,
        "effective_profile": effective.name,
        "auto_conservative": auto_conservative,
        "negative_roi_alert": roi < 0.0,
        "roi_units": round(roi, 2),
    }


def apply_recommendation_policy(
    signal: dict[str, Any],
    learning_context: dict[str, Any] | None,
    *,
    bankroll: float,
    unit_percent: float,
    selected_profile: str | None,
) -> dict[str, Any]:
    signal = dict(signal or {})
    learning_context = learning_context or {}
    profile, profile_meta = resolve_profile(selected_profile, learning_context)

    market_name = _market_name(signal)
    market_category = _market_category(market_name)
    league_name = _league_name(signal)
    odd = _safe_float(signal.get("target_odds"))
    estimated_probability = _resolve_estimated_probability(signal)
    implied_probability = (1.0 / odd) if odd and odd > 1 else None
    expected_value = (
        (estimated_probability * odd) - 1.0
        if odd and odd > 1 and estimated_probability is not None
        else None
    )
    market_quality_score = _market_quality_score(signal, odd)
    historical_market = _row_for_subject(learning_context.get("market_breakdown"), market_category)
    historical_league = _row_for_subject(learning_context.get("league_breakdown"), league_name)
    historical_reference = signal.get("historical_context") if isinstance(signal.get("historical_context"), dict) else {}
    historical_performance_score = _historical_score(signal, historical_market, historical_league)
    league_reliability_score = _league_reliability_score(signal, historical_league)
    odds_validity_score = _odds_validity_score(odd, profile)
    model_agreement_score = _model_agreement_score(signal)
    odd_stability_score = _odd_stability_score(signal, odd)
    data_completeness = max(0.0, min(1.0, _safe_float(signal.get("data_quality")) / 100.0))
    historical_sample = max(
        _safe_int(historical_reference.get("league_sample_size")),
        _safe_int(historical_reference.get("usable_training_matches")),
    )
    historical_fit_score = _safe_float(historical_reference.get("market_fit_score"), None)
    historical_classification = str(historical_reference.get("league_classification") or "").strip().lower()

    confidence_score = (
        (data_completeness * 0.24)
        + (league_reliability_score * 0.18)
        + (historical_performance_score * 0.20)
        + (odds_validity_score * 0.16)
        + (model_agreement_score * 0.10)
        + (odd_stability_score * 0.12)
    )
    confidence_score = max(0.0, min(1.0, confidence_score))

    ev_score = _ev_score(expected_value)

    reasons: list[str] = []
    entry_allowed = True
    recommendation = "Entrada moderada"
    risk_level = "Medio"
    recommendation_order = 2

    if expected_value is None:
        entry_allowed = False
        recommendation = "Ignorar"
        risk_level = "Sem valor"
        recommendation_order = 4
        reasons.append("Odd invalida ou ausente para calcular valor esperado.")
    elif expected_value <= 0:
        entry_allowed = False
        recommendation = "Ignorar"
        risk_level = "Sem valor"
        recommendation_order = 4
        reasons.append("EV ficou zerado ou negativo.")
    elif expected_value < 0.015:
        entry_allowed = False
        recommendation = "Valor baixo"
        risk_level = "Alto"
        recommendation_order = 4
        reasons.append("EV abaixo de 1.5%.")
    elif expected_value < profile.min_ev:
        entry_allowed = False
        recommendation = "Monitorar"
        risk_level = "Medio/Alto"
        recommendation_order = 3
        reasons.append(
            f"EV abaixo do minimo do perfil {profile.name} ({_pct(profile.min_ev)})."
        )
    elif expected_value >= 0.08:
        recommendation = "Entrada forte"
        risk_level = "Medio"
        recommendation_order = 1
    else:
        recommendation = "Entrada moderada"
        risk_level = "Medio"
        recommendation_order = 2

    if confidence_score < profile.min_confidence_score:
        entry_allowed = False
        if recommendation != "Ignorar":
            recommendation = "Aguardar"
            recommendation_order = 3
        reasons.append(
            f"Confianca insuficiente ({confidence_score:.2f} < {profile.min_confidence_score:.2f})."
        )

    if odd is None or odd < profile.odd_min or odd > profile.odd_max:
        entry_allowed = False
        if recommendation != "Ignorar":
            recommendation = "Aguardar"
            recommendation_order = 3
        reasons.append("Odd fora da faixa operacional.")

    if _safe_int(signal.get("data_quality")) < 60:
        entry_allowed = False
        if recommendation != "Ignorar":
            recommendation = "Aguardar"
            recommendation_order = 3
        reasons.append("Dados incompletos para sustentar a entrada.")

    if historical_league and historical_league.get("status") == "observacao":
        reasons.append("Liga em observacao por historico recente negativo.")
        confidence_score = max(0.0, confidence_score - 0.08)
        if recommendation == "Entrada forte":
            recommendation = "Entrada moderada"
            recommendation_order = 2
        if confidence_score < profile.min_confidence_score:
            entry_allowed = False
            recommendation = "Aguardar"
            recommendation_order = 3

    if historical_classification.startswith("evitar") and historical_sample >= 25:
        entry_allowed = False
        recommendation = "Aguardar"
        recommendation_order = 3
        reasons.append("Base historica de 3 anos marca esta liga como evitar no momento.")
    elif historical_classification.startswith("em observ") and historical_sample >= 20:
        reasons.append("Base historica pede cautela extra para esta liga.")
        confidence_score = max(0.0, confidence_score - 0.05)
        if confidence_score < profile.min_confidence_score:
            entry_allowed = False
            recommendation = "Aguardar"
            recommendation_order = 3

    if historical_fit_score is not None:
        if historical_fit_score < 0.35 and historical_sample >= 20:
            entry_allowed = False
            recommendation = "Monitorar"
            recommendation_order = 3
            reasons.append("Comparacao com a base historica nao confirmou este mercado.")
        elif historical_fit_score >= 0.65 and historical_sample >= 20:
            reasons.append("Comparacao historica reforcou a leitura do mercado.")
    elif historical_sample and historical_sample < 10:
        reasons.append("Base historica ainda curta para calibrar este confronto.")

    final_score = round(
        (
            (ev_score * 0.40)
            + (confidence_score * 0.35)
            + (market_quality_score * 0.15)
            + (historical_performance_score * 0.10)
        )
        * 100.0,
        1,
    )

    stake_value, stake_units, kelly_fraction = _stake_suggestion(
        bankroll=bankroll,
        unit_percent=unit_percent,
        odd=odd,
        probability=estimated_probability,
        confidence_score=confidence_score,
        expected_value=expected_value,
        entry_allowed=entry_allowed,
        profile=profile,
    )

    if not entry_allowed:
        signal["action"] = "AGUARDAR" if str(signal.get("action") or "").upper() != "SAIR" else "SAIR"

    if entry_allowed and recommendation == "Entrada forte":
        signal["decision_class"] = "ENTRA_FORTE"
    elif entry_allowed:
        signal["decision_class"] = "ENTRA_LEVE"
    elif recommendation in {"Aguardar", "Monitorar"}:
        signal["decision_class"] = "ESPERA"
    else:
        signal["decision_class"] = "NO_BET"

    if not reasons and entry_allowed:
        reasons.append("Sinal passou nos filtros de EV, confianca, odd e historico.")

    explanation = _build_explanation(
        signal=signal,
        recommendation=recommendation,
        entry_allowed=entry_allowed,
        expected_value=expected_value,
        confidence_score=confidence_score,
        odd=odd,
        implied_probability=implied_probability,
        historical_league=historical_league,
        historical_reference=historical_reference,
        reasons=reasons,
    )

    signal.update(
        {
            "risk_profile": profile_meta["selected_profile"],
            "effective_risk_profile": profile_meta["effective_profile"],
            "auto_conservative": profile_meta["auto_conservative"],
            "auto_conservative_suggested": profile_meta["negative_roi_alert"],
            "recommended_profile": "conservador" if profile_meta["negative_roi_alert"] else profile_meta["selected_profile"],
            "market_category": market_category,
            "league_name": league_name,
            "estimated_probability": round(estimated_probability, 4) if estimated_probability is not None else None,
            "implied_probability": round(implied_probability, 4) if implied_probability is not None else None,
            "value_edge": round(expected_value, 4) if expected_value is not None else signal.get("value_edge"),
            "expected_value": round(expected_value, 4) if expected_value is not None else None,
            "confidence_score": round(confidence_score, 4),
            "confidence_score_pct": round(confidence_score * 100.0, 1),
            "market_quality_score": round(market_quality_score, 4),
            "historical_performance_score": round(historical_performance_score, 4),
            "league_reliability_score": round(league_reliability_score, 4),
            "odds_validity_score": round(odds_validity_score, 4),
            "model_agreement_score": round(model_agreement_score, 4),
            "odd_stability_score": round(odd_stability_score, 4),
            "final_score": round(final_score, 1),
            "entry_score": max(_safe_int(signal.get("entry_score")), int(round(final_score))),
            "risk_score": min(_safe_int(signal.get("risk_score"), 100), int(round(100.0 - final_score))),
            "recommendation": recommendation,
            "recommendation_order": recommendation_order,
            "risk_level": risk_level,
            "entry_allowed": bool(entry_allowed),
            "stake_value": round(stake_value, 2),
            "stake_units": round(stake_units, 2),
            "stake_fraction_of_bankroll": round((stake_value / bankroll), 4) if bankroll > 0 else 0.0,
            "kelly_fraction": round(kelly_fraction, 4),
            "decision_reasons": reasons,
            "ai_explanation": explanation,
        }
    )
    return signal


def _stake_suggestion(
    *,
    bankroll: float,
    unit_percent: float,
    odd: float | None,
    probability: float | None,
    confidence_score: float,
    expected_value: float | None,
    entry_allowed: bool,
    profile: RiskProfile,
) -> tuple[float, float, float]:
    if (
        not entry_allowed
        or odd is None
        or odd <= 1
        or probability is None
        or probability <= 0
        or expected_value is None
        or expected_value <= 0
        or confidence_score < profile.min_confidence_score
    ):
        return 0.0, 0.0, 0.0
    b = odd - 1.0
    q = 1.0 - probability
    full_kelly = ((odd - 1.0) * probability - q) / b if b > 0 else 0.0
    if full_kelly <= 0:
        return 0.0, 0.0, 0.0
    raw_stake = bankroll * full_kelly * profile.kelly_multiplier
    capped_stake = min(raw_stake, bankroll * profile.stake_cap_fraction)
    unit_value = bankroll * max(0.0001, unit_percent / 100.0)
    stake_units = capped_stake / unit_value if unit_value > 0 else 0.0
    return round(capped_stake, 2), round(stake_units, 2), round(full_kelly, 4)


def _build_explanation(
    *,
    signal: dict[str, Any],
    recommendation: str,
    entry_allowed: bool,
    expected_value: float | None,
    confidence_score: float,
    odd: float | None,
    implied_probability: float | None,
    historical_league: dict[str, Any] | None,
    historical_reference: dict[str, Any] | None,
    reasons: list[str],
) -> str:
    market = _market_name(signal)
    probability = _resolve_estimated_probability(signal)
    historical_summary = ""
    if historical_reference:
        summary = str(historical_reference.get("comparison_summary") or "").strip()
        if summary:
            historical_summary = f" {summary}"
    if entry_allowed:
        league_note = ""
        if historical_league:
            league_note = (
                f" A liga vem com ROI {historical_league.get('roi_units', 0)}% "
                f"em {historical_league.get('entries', 0)} entradas."
            )
        return (
            f"{recommendation} em {market} porque a probabilidade estimada foi {_pct(probability)}, "
            f"a probabilidade implicita da odd foi {_pct(implied_probability)}, "
            f"o EV ficou em {_pct(expected_value)} e a confianca fechou em {confidence_score:.2f}.{league_note}{historical_summary}"
        )
    return (
        f"Entrada nao liberada porque o EV ficou em {_pct(expected_value)}, "
        f"a confianca foi {confidence_score:.2f}, a odd operacional ficou em {odd if odd is not None else '-'} "
        f"e os filtros marcaram: {'; '.join(reasons)}{historical_summary}"
    )


def _resolve_estimated_probability(signal: dict[str, Any]) -> float | None:
    probability = _safe_float(signal.get("estimated_probability"))
    if probability > 0:
        return max(0.01, min(0.99, probability))
    confidence = _safe_float(signal.get("confidence"))
    if confidence > 0:
        if confidence > 1:
            confidence /= 100.0
        return max(0.01, min(0.99, confidence))
    return None


def _market_quality_score(signal: dict[str, Any], odd: float | None) -> float:
    data_quality = _safe_float(signal.get("data_quality")) / 100.0
    minute = _safe_int((signal.get("game") or {}).get("minute"))
    action = str(signal.get("action") or "").upper()
    market_name = _market_name(signal)
    score = data_quality * 0.55
    if odd is not None and odd > 1:
        score += 0.20
    if 15 <= minute <= 65:
        score += 0.15
    elif minute >= 80 or minute < 10:
        score -= 0.12
    if any(token in market_name.lower() for token in ("1x2", "gols", "under", "over", "btts")):
        score += 0.10
    if action == "ENTRAR":
        score += 0.05
    return max(0.0, min(1.0, score))


def _historical_score(
    signal: dict[str, Any],
    market_row: dict[str, Any] | None,
    league_row: dict[str, Any] | None,
) -> float:
    precomputed = _safe_float(signal.get("historical_performance_score"), None)
    if precomputed is not None and precomputed > 0:
        return max(0.0, min(1.0, precomputed))
    scores = []
    for row in (market_row, league_row):
        if not row:
            continue
        roi = _safe_float(row.get("roi_units"))
        hit_rate = _safe_float(row.get("hit_rate")) / 100.0
        entries = max(1, _safe_int(row.get("entries") or row.get("total")))
        roi_score = max(0.0, min(1.0, (roi + 15.0) / 30.0))
        sample_score = max(0.0, min(1.0, entries / 25.0))
        scores.append((roi_score * 0.50) + (hit_rate * 0.30) + (sample_score * 0.20))
    if not scores:
        return 0.40
    return max(0.0, min(1.0, sum(scores) / len(scores)))


def _league_reliability_score(signal: dict[str, Any], league_row: dict[str, Any] | None) -> float:
    precomputed = _safe_float(signal.get("league_reliability_score"), None)
    if precomputed is not None and precomputed > 0:
        return max(0.0, min(1.0, precomputed))
    if not league_row:
        return 0.45
    entries = _safe_int(league_row.get("entries") or league_row.get("total"))
    hit_rate = _safe_float(league_row.get("hit_rate")) / 100.0
    roi = _safe_float(league_row.get("roi_units"))
    score = 0.25 + min(0.30, entries / 60.0) + min(0.25, hit_rate * 0.35)
    if roi < 0:
        score -= min(0.20, abs(roi) / 100.0)
    return max(0.0, min(1.0, score))


def _odds_validity_score(odd: float | None, profile: RiskProfile) -> float:
    if odd is None or odd <= 1:
        return 0.0
    if odd < profile.odd_min or odd > profile.odd_max:
        return 0.15
    midpoint = (profile.odd_min + profile.odd_max) / 2.0
    deviation = abs(odd - midpoint) / max(0.01, midpoint)
    return max(0.30, min(1.0, 1.0 - deviation))


def _model_agreement_score(signal: dict[str, Any]) -> float:
    confidence_score = _safe_float(signal.get("confidence")) / 100.0
    brain_confidence = _safe_float(signal.get("brain_confidence")) / 100.0
    entry_score = _safe_float(signal.get("entry_score")) / 100.0
    candidates = [value for value in (confidence_score, brain_confidence, entry_score) if value > 0]
    if len(candidates) < 2:
        return 0.55
    spread = max(candidates) - min(candidates)
    return max(0.10, min(1.0, 1.0 - spread))


def _odd_stability_score(signal: dict[str, Any], odd: float | None) -> float:
    if odd is None or odd <= 1:
        return 0.0
    fair = _safe_float(signal.get("fair_odds"))
    if fair <= 0:
        return 0.55
    diff = abs(odd - fair) / max(0.01, fair)
    return max(0.10, min(1.0, 1.0 - min(diff, 1.0)))


def _ev_score(expected_value: float | None) -> float:
    if expected_value is None:
        return 0.0
    if expected_value <= 0:
        return 0.0
    return max(0.0, min(1.0, expected_value / 0.10))


def _row_for_subject(rows: Any, subject: str) -> dict[str, Any] | None:
    target = str(subject or "").strip().lower()
    if not target:
        return None
    for row in rows or []:
        name = str((row or {}).get("name") or (row or {}).get("category") or "").strip().lower()
        if name == target:
            return row
    return None


def _market_name(signal: dict[str, Any]) -> str:
    return str(signal.get("entry_market") or signal.get("market") or "-").strip() or "-"


def _market_category(market_name: str) -> str:
    text = str(market_name or "").strip().lower()
    if "1x2" in text or "resultado final" in text:
        return "1X2"
    if "btts" in text or "ambos marcam" in text:
        return "BTTS"
    if "over 2.5" in text or ("gols" in text and "over" in text and "2.5" in text):
        return "Over 2.5"
    if "under 2.5" in text or ("gols" in text and "under" in text and "2.5" in text):
        return "Under 2.5"
    return "Outros"


def _league_name(signal: dict[str, Any]) -> str:
    game = signal.get("game") or {}
    return str(game.get("league") or game.get("division") or "-").strip() or "-"


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{round(value * 100.0, 1)}%"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
