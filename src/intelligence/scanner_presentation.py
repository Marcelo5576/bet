from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import unicodedata


PROFILE_RULES: dict[str, dict[str, float]] = {
    "conservador": {
        "min_ev": 0.08,
        "min_confidence": 0.75,
        "min_score": 70.0,
        "odd_min": 1.60,
        "odd_max": 2.50,
        "stake_cap": 0.01,
    },
    "moderado": {
        "min_ev": 0.05,
        "min_confidence": 0.65,
        "min_score": 65.0,
        "odd_min": 1.60,
        "odd_max": 3.00,
        "stake_cap": 0.03,
    },
    "agressivo": {
        "min_ev": 0.03,
        "min_confidence": 0.60,
        "min_score": 60.0,
        "odd_min": 1.60,
        "odd_max": 3.50,
        "stake_cap": 0.03,
    },
}

DEFAULT_PROFILE = "moderado"
MONITOR_CONFIDENCE_MIN = 0.55
MONITOR_SCORE_MIN = 55
MONITOR_EV_MIN = 0.03
MARKET_GUARD_TTL_SECONDS = 300
MARKET_GUARD_MIN_ENTRIES = 10
MARKET_GUARD_MAX_ROI = -10.0
_MARKET_GUARD_CACHE: tuple[float, dict[str, dict[str, Any]]] | None = None


def build_decision_view_model(match_or_signal: dict[str, Any]) -> dict[str, Any]:
    signal = dict(match_or_signal or {})
    game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
    profile_name, profile = _resolve_profile(signal)
    home, away = _resolve_match_label(signal, game)
    minute = _safe_int(game.get("minute") or signal.get("minute"))
    home_goals = _safe_int(game.get("home_goals") or signal.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals") or signal.get("away_goals"))
    market = str(signal.get("entry_market") or signal.get("market") or "-").strip() or "-"
    line = str(signal.get("entry_line") or signal.get("line") or "").strip()
    market_label = market if not line or line == "-" else f"{market} · {line}"
    odd = _safe_float(signal.get("entry_odds") or signal.get("target_odds") or signal.get("odds"), None)
    ev = _safe_float(signal.get("expected_value"), None)
    implied_probability = _safe_float(signal.get("implied_probability"), None)
    estimated_probability = _safe_float(
        signal.get("estimated_probability")
        or signal.get("probability")
        or signal.get("brain_probability"),
        None,
    )
    confidence_ratio = _resolve_confidence_ratio(signal)
    confidence_pct = round(confidence_ratio * 100.0, 1)
    score_value = _resolve_score(signal)
    recommendation = str(signal.get("recommendation") or "").strip()
    selection = str(signal.get("entry_selection") or signal.get("selection") or "").strip()
    raw_action = str(signal.get("action") or "").strip().upper()
    risk_level = str(signal.get("risk_level") or _fallback_risk(score_value, confidence_pct)).strip() or "Médio"
    data_quality = _safe_int(signal.get("data_quality"))
    market_quality_score = _resolve_market_quality_score(signal, odd, data_quality)
    historical_performance_score = _safe_float(signal.get("historical_performance_score"), None)
    entry_allowed = bool(signal.get("entry_allowed"))
    updated_at = str(
        signal.get("updated_at")
        or signal.get("last_update_at")
        or signal.get("generated_at")
        or signal.get("created_at")
        or "-"
    ).strip() or "-"
    source = str(
        signal.get("source")
        or signal.get("provider")
        or game.get("source")
        or "scanner"
    ).strip() or "scanner"
    ai_explanation = str(signal.get("ai_explanation") or signal.get("reason") or "").strip()
    minute_danger = _minute_is_dangerous(minute)
    stats_present = _has_stats(signal, game) or data_quality >= 45
    fixture_present = bool(
        game.get("game_id")
        or signal.get("game_id")
        or signal.get("fixture_id")
        or (home and home != "-")
        or (away and away != "-")
    )
    odds_confirmed = odd is not None and odd > 1
    recommendation_lower = recommendation.lower()
    source_error = "erro" in ai_explanation.lower() or "falha" in ai_explanation.lower()

    odd_ok = odds_confirmed and profile["odd_min"] <= odd <= profile["odd_max"]
    confidence_ok = confidence_ratio >= profile["min_confidence"]
    score_ok = score_value >= profile["min_score"]
    ev_required = ev is not None
    ev_ok = (ev is not None and ev >= profile["min_ev"]) if ev_required else False
    ev_negative = ev is not None and ev <= 0
    confidence_near = confidence_ratio >= MONITOR_CONFIDENCE_MIN
    score_near = score_value >= MONITOR_SCORE_MIN
    ev_near = ev is not None and MONITOR_EV_MIN <= ev < profile["min_ev"]
    risk_high = _is_high_risk(risk_level)
    contradictory_signal = raw_action in {"SEGURAR", "SAIR"} or "contradit" in ai_explanation.lower()
    market_guard = _market_learning_guard(signal, market=market, selection=selection, home=home)
    market_blocked = bool(market_guard)
    incomplete_data = (
        not fixture_present
        or market == "-"
        or source_error
        or ("sem dados" in recommendation_lower)
        or (not stats_present and data_quality < 35)
    )
    missing_essentials = (not odds_confirmed and not stats_present) or incomplete_data

    checklist = [
        _check_item("Odd OK", odd_ok, "Odd fora da faixa", neutral=not odds_confirmed),
        _check_item("Confiança OK", confidence_ok, "Confiança baixa"),
        _check_item("Score OK", score_ok, "Score baixo"),
        _check_item("EV OK", ev_ok, "EV insuficiente", neutral=ev is None),
        _check_item(
            "Mercado estável",
            not minute_danger and not risk_high and not contradictory_signal,
            "Mercado instável",
            neutral=not stats_present,
        ),
    ]

    positive_reasons = _collect_positive_reasons(
        odd_ok=odd_ok,
        confidence_ok=confidence_ok,
        score_ok=score_ok,
        ev_ok=ev_ok,
        minute_danger=minute_danger,
        market_quality_score=market_quality_score,
    )
    blocking_reasons = _collect_blocking_reasons(
        fixture_present=fixture_present,
        stats_present=stats_present,
        odd=odd,
        odd_ok=odd_ok,
        confidence_ratio=confidence_ratio,
        score_value=score_value,
        ev=ev,
        risk_high=risk_high,
        contradictory_signal=contradictory_signal,
        minute_danger=minute_danger,
        profile=profile,
    )
    if market_guard:
        guard_reason = str(market_guard.get("reason") or "Mercado rebaixado por aprendizado histórico.").strip()
        blocking_reasons.insert(0, guard_reason)
        checklist.append(
            {
                "state": "negative",
                "passed": False,
                "text": "❌ Histórico 1X2 ruim",
            }
        )
        entry_allowed = False
        risk_level = "Alto"
        historical_performance_score = 0.0

    if missing_essentials:
        decision_status = "NO_DATA"
    elif (
        entry_allowed
        and odd_ok
        and confidence_ok
        and score_ok
        and ev_ok
        and not risk_high
        and not contradictory_signal
        and not market_blocked
        and not minute_danger
        and stats_present
    ):
        decision_status = "ENTER_NOW"
    elif (
        ev_negative
        or not odd_ok
        or confidence_ratio < MONITOR_CONFIDENCE_MIN
        or score_value < MONITOR_SCORE_MIN
        or risk_high
        or contradictory_signal
        or market_blocked
    ):
        decision_status = "DO_NOT_ENTER"
    elif (
        confidence_near
        or score_near
        or ev_near
        or recommendation_lower in {"aguardar", "monitorar"}
        or minute_danger
        or not stats_present
        or not odds_confirmed
    ):
        decision_status = (
            "MONITOR_ONLY"
            if (not stats_present or not odds_confirmed or data_quality < 45)
            else "WAIT_CONFIRMATION"
        )
    else:
        decision_status = "MONITOR_ONLY"

    decision_meta = _decision_meta(decision_status)
    action_label = decision_meta["action_label"]
    risk_level = _normalize_risk(risk_level, decision_status)
    confidence_label = f"{confidence_pct:.0f}% {_confidence_badge(confidence_pct)}"
    score_label = f"{score_value}/100 {_score_badge(score_value)}"
    ev_label = _ev_badge_label(ev)
    odd_label = _odd_badge_label(odd, profile)
    main_reason = _main_reason(
        decision_status=decision_status,
        positive_reasons=positive_reasons,
        blocking_reasons=blocking_reasons,
        ai_explanation=ai_explanation,
        minute=minute,
        stats_present=stats_present,
        odds_confirmed=odds_confirmed,
    )

    card_priority = _card_priority(
        decision_status=decision_status,
        ev=ev,
        confidence_ratio=confidence_ratio,
        score_value=score_value,
        market_quality_score=market_quality_score,
        historical_performance_score=historical_performance_score,
    )

    formatted_message = "\n".join(
        [
            "━━━━━━━━━━━━━━",
            f"{decision_meta['emoji']} {decision_meta['label']}",
            f"⚽ {home} x {away}",
            f"⏱ {minute}' | Placar {home_goals}x{away_goals}",
            f"🎯 Mercado: {market_label}",
            f"💰 Odd: {odd_label}",
            f"📊 Confiança: {confidence_label}",
            f"📈 Score: {score_label}",
            f"💹 EV: {ev_label}",
            f"⚠️ Risco: {risk_level}",
            f"🧠 Motivo: {main_reason}",
            f"👉 Ação: {action_label}",
            "Checklist:",
            *[item["text"] for item in checklist],
            "━━━━━━━━━━━━━━",
        ]
    )

    model = {
        "decision_status": decision_status,
        "decision_label": decision_meta["label"],
        "decision_emoji": decision_meta["emoji"],
        "decision_color": decision_meta["color"],
        "decision_code": decision_meta["code"],
        "status_class": decision_meta["css_class"],
        "status_label": decision_meta["label"],
        "status_emoji": decision_meta["emoji"],
        "status_banner": f"{decision_meta['emoji']} {decision_meta['label']}",
        "action_label": action_label,
        "main_reason": main_reason,
        "risk_level": risk_level,
        "risk_color": _risk_color(risk_level),
        "confidence_label": confidence_label,
        "confidence_badge": _confidence_badge(confidence_pct),
        "confidence_pct": confidence_pct,
        "confidence_ratio": confidence_ratio,
        "score_label": score_label,
        "score_badge": _score_badge(score_value),
        "score_value": score_value,
        "ev_label": ev_label,
        "ev_value": ev,
        "odd_label": odd_label,
        "odd_value": odd,
        "implied_probability": implied_probability,
        "estimated_probability": estimated_probability,
        "checklist": checklist,
        "blocking_reasons": blocking_reasons,
        "positive_reasons": positive_reasons,
        "card_priority": card_priority,
        "formatted_message": formatted_message,
        "match_label": f"{home} x {away}",
        "market_label": market_label,
        "entry_allowed": decision_status == "ENTER_NOW",
        "minute_danger": minute_danger,
        "profile_name": profile_name,
        "profile_label": _profile_label(profile_name),
        "criteria": {
            "min_ev": profile["min_ev"],
            "min_confidence": profile["min_confidence"],
            "min_score": profile["min_score"],
            "odd_min": profile["odd_min"],
            "odd_max": profile["odd_max"],
            "stake_cap": profile["stake_cap"],
        },
        "source_label": source,
        "updated_at": updated_at,
        "odds_confirmed": odds_confirmed,
        "stats_confirmed": stats_present,
        "has_fixture": fixture_present,
        "data_quality": data_quality,
        "market_quality_score": market_quality_score,
        "market_learning_guard": market_guard,
    }
    return model


def build_scanner_decision(match: dict[str, Any]) -> dict[str, Any]:
    return build_decision_view_model(match)


def _resolve_profile(match: dict[str, Any]) -> tuple[str, dict[str, float]]:
    requested = str(
        match.get("effective_risk_profile")
        or match.get("risk_profile")
        or ((match.get("policy") or {}).get("effective_risk_profile") if isinstance(match.get("policy"), dict) else "")
        or ((match.get("policy") or {}).get("risk_profile") if isinstance(match.get("policy"), dict) else "")
        or DEFAULT_PROFILE
    ).strip().lower()
    if requested not in PROFILE_RULES:
        requested = DEFAULT_PROFILE
    return requested, PROFILE_RULES[requested]


def _resolve_match_label(match: dict[str, Any], game: dict[str, Any]) -> tuple[str, str]:
    home = str(game.get("home") or match.get("home") or _split_match(match.get("match"))[0] or "-")
    away = str(game.get("away") or match.get("away") or _split_match(match.get("match"))[1] or "-")
    return home, away


def _has_stats(signal: dict[str, Any], game: dict[str, Any]) -> bool:
    keys = (
        "home_pressure",
        "away_pressure",
        "home_shots_on",
        "away_shots_on",
        "home_shots",
        "away_shots",
        "home_attacks",
        "away_attacks",
        "home_danger",
        "away_danger",
        "xg_home",
        "xg_away",
    )
    for key in keys:
        value = game.get(key)
        if value is None:
            value = signal.get(key)
        if _safe_float(value, None) is not None:
            return True
    return False


def _resolve_market_quality_score(signal: dict[str, Any], odd: float | None, data_quality: int) -> float:
    explicit = _safe_float(signal.get("market_quality_score"), None)
    if explicit is not None:
        return max(0.0, min(1.0, explicit))
    quality = min(1.0, max(0.0, data_quality / 100.0))
    if odd is None:
        return round(quality * 0.55, 4)
    midpoint = 2.20
    odd_factor = max(0.35, 1.0 - (abs(odd - midpoint) / max(0.1, midpoint)))
    return round(max(0.0, min(1.0, (quality * 0.65) + (odd_factor * 0.35))), 4)


def _market_learning_guard(
    signal: dict[str, Any],
    *,
    market: str,
    selection: str,
    home: str,
) -> dict[str, Any] | None:
    market_key = _normalize_learning_market(signal, market=market, selection=selection, home=home)
    if not market_key:
        return None
    summary = _historical_market_guard_summary().get(market_key)
    if not summary:
        return None
    entries = int(summary.get("entries") or 0)
    roi = float(summary.get("roi_on_staked") or 0.0)
    if entries < MARKET_GUARD_MIN_ENTRIES or roi > MARKET_GUARD_MAX_ROI:
        return None
    label = str(summary.get("label") or market_key)
    return {
        "active": True,
        "market": market_key,
        "label": label,
        "entries": entries,
        "roi_on_staked": roi,
        "profit_paper": float(summary.get("profit_paper") or 0.0),
        "reason": (
            f"{label} rebaixado: odds historicas reais mostram ROI paper "
            f"{roi:+.1f}% em {entries} entradas."
        ),
    }


def _normalize_learning_market(
    signal: dict[str, Any],
    *,
    market: str,
    selection: str,
    home: str,
) -> str | None:
    explicit = _clean_token(
        signal.get("learning_market")
        or signal.get("market_key")
        or signal.get("market_code")
        or signal.get("entry_market_key")
    )
    if explicit in {"match_winner_home", "home_win", "1x2_home"}:
        return "match_winner_home"
    market_text = _clean_token(
        " ".join(
            str(value or "")
            for value in (
                market,
                signal.get("entry_market"),
                signal.get("market_category"),
                signal.get("market_group"),
            )
        )
    )
    selection_text = _clean_token(selection or signal.get("selection") or signal.get("entry_selection"))
    home_text = _clean_token(home)
    is_1x2 = any(
        token in market_text
        for token in (
            "1x2",
            "resultado final",
            "match winner",
            "vencedor",
            "moneyline",
        )
    )
    is_home_selection = selection_text in {"home", "casa", "mandante", "time casa"} or (
        bool(selection_text and home_text) and selection_text == home_text
    )
    if is_1x2 and is_home_selection:
        return "match_winner_home"
    return None


def _historical_market_guard_summary() -> dict[str, dict[str, Any]]:
    global _MARKET_GUARD_CACHE
    now = time.time()
    if _MARKET_GUARD_CACHE and now - _MARKET_GUARD_CACHE[0] < MARKET_GUARD_TTL_SECONDS:
        return _MARKET_GUARD_CACHE[1]
    summary = _load_historical_market_guard_summary()
    _MARKET_GUARD_CACHE = (now, summary)
    return summary


def _load_historical_market_guard_summary() -> dict[str, dict[str, Any]]:
    db_file = Path(os.getenv("FOOTBALL_RESEARCH_DB_FILE", "data/football_quant_research.db"))
    if not db_file.exists():
        return {}
    grouped: dict[str, dict[str, Any]] = {}
    try:
        with sqlite3.connect(str(db_file)) as con:
            rows = con.execute(
                "SELECT payload_json FROM learning_events WHERE event_type = ?",
                ("historical_odds_ev",),
            ).fetchall()
    except sqlite3.Error:
        return {}
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        market_key = str(payload.get("market") or "").strip()
        if not market_key:
            continue
        item = grouped.setdefault(
            market_key,
            {
                "label": _market_label(market_key),
                "analyzed": 0,
                "entries": 0,
                "wins": 0,
                "losses": 0,
                "profit_paper": 0.0,
                "stake_paper": 0.0,
            },
        )
        item["analyzed"] += 1
        stake = _safe_float(payload.get("stake_paper"), 0.0) or 0.0
        profit = _safe_float(payload.get("profit_paper"), 0.0) or 0.0
        result = str(payload.get("result") or "").upper()
        if stake > 0 or bool(payload.get("entry_allowed")):
            item["entries"] += 1
            item["stake_paper"] += stake
            item["profit_paper"] += profit
            if result == "WIN":
                item["wins"] += 1
            elif result == "LOSS":
                item["losses"] += 1
    for item in grouped.values():
        stake = float(item.get("stake_paper") or 0.0)
        item["roi_on_staked"] = (float(item.get("profit_paper") or 0.0) / stake * 100.0) if stake > 0 else 0.0
    return grouped


def _market_label(market_key: str) -> str:
    return {
        "match_winner_home": "1X2 casa",
        "over_2_5": "Over 2.5",
        "btts_yes": "BTTS sim",
    }.get(market_key, market_key)


def _clean_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _collect_positive_reasons(
    *,
    odd_ok: bool,
    confidence_ok: bool,
    score_ok: bool,
    ev_ok: bool,
    minute_danger: bool,
    market_quality_score: float,
) -> list[str]:
    reasons: list[str] = []
    if odd_ok:
        reasons.append("Odd dentro da faixa operacional.")
    if confidence_ok:
        reasons.append("Confiança acima do mínimo.")
    if score_ok:
        reasons.append("Score acima do mínimo.")
    if ev_ok:
        reasons.append("EV acima do critério do perfil.")
    if not minute_danger:
        reasons.append("Jogo fora da janela mais perigosa.")
    if market_quality_score >= 0.65:
        reasons.append("Mercado com qualidade histórica boa.")
    return reasons


def _collect_blocking_reasons(
    *,
    fixture_present: bool,
    stats_present: bool,
    odd: float | None,
    odd_ok: bool,
    confidence_ratio: float,
    score_value: int,
    ev: float | None,
    risk_high: bool,
    contradictory_signal: bool,
    minute_danger: bool,
    profile: dict[str, float],
) -> list[str]:
    reasons: list[str] = []
    if not fixture_present:
        reasons.append("Fixture inválida ou ausente.")
    if odd is None:
        reasons.append("Odd não confirmada.")
    elif not odd_ok:
        if odd < profile["odd_min"]:
            reasons.append("Odd muito baixa, risco não compensa.")
        elif odd > profile["odd_max"]:
            reasons.append("Odd muito alta, baixa confiabilidade.")
    if confidence_ratio < profile["min_confidence"]:
        reasons.append(f"Confiança abaixo de {int(profile['min_confidence'] * 100)}%.")
    if score_value < int(profile["min_score"]):
        reasons.append(f"Score abaixo de {int(profile['min_score'])}/100.")
    if ev is None:
        reasons.append("EV não confirmado.")
    elif ev <= 0:
        reasons.append("EV negativo.")
    elif ev < profile["min_ev"]:
        reasons.append(f"EV abaixo de {int(profile['min_ev'] * 100)}%.")
    if not stats_present:
        reasons.append("Estatísticas insuficientes.")
    if risk_high:
        reasons.append("Risco alto no contexto atual.")
    if contradictory_signal:
        reasons.append("Sinal contraditório ou em gestão.")
    if minute_danger:
        reasons.append("Momento sensível do jogo.")
    return reasons


def _main_reason(
    *,
    decision_status: str,
    positive_reasons: list[str],
    blocking_reasons: list[str],
    ai_explanation: str,
    minute: int,
    stats_present: bool,
    odds_confirmed: bool,
) -> str:
    primary_positive = positive_reasons[0] if positive_reasons else ""
    primary_block = blocking_reasons[0] if blocking_reasons else ""
    if decision_status == "ENTER_NOW":
        return (
            "Entrada aprovada porque odd, confiança, score e EV passaram nos critérios."
            if not ai_explanation
            else ai_explanation
        )
    if decision_status == "WAIT_CONFIRMATION":
        base = "Sinal promissor, mas ainda falta confirmação."
        if minute >= 75:
            base = "Momento delicado do jogo, melhor esperar confirmação."
        elif primary_positive:
            base = f"Aguardar: {primary_positive[:-1].lower() if primary_positive.endswith('.') else primary_positive.lower()}."
        return _merge_reason(base, ai_explanation)
    if decision_status == "MONITOR_ONLY":
        if not odds_confirmed:
            base = "Há leitura útil, mas a odd ainda não foi confirmada."
        elif not stats_present:
            base = "Há poucos dados estatísticos para uma entrada clara."
        else:
            base = "Há dados úteis, mas não há entrada clara agora."
        return _merge_reason(base, ai_explanation)
    if decision_status == "DO_NOT_ENTER":
        base = primary_block or "Critérios operacionais insuficientes."
        if base and not base.endswith("."):
            base += "."
        lead = f"Não entrar: {base}"
        return _merge_reason(lead, ai_explanation)
    return _merge_reason("Aguardando dados confiáveis da API e do scanner.", ai_explanation)


def _card_priority(
    *,
    decision_status: str,
    ev: float | None,
    confidence_ratio: float,
    score_value: int,
    market_quality_score: float,
    historical_performance_score: float | None,
) -> int:
    decision_boost = {
        "ENTER_NOW": 18,
        "WAIT_CONFIRMATION": 10,
        "MONITOR_ONLY": 4,
        "DO_NOT_ENTER": -10,
        "NO_DATA": -18,
    }.get(decision_status, 0)
    historical = market_quality_score if historical_performance_score is None else max(0.0, min(1.0, historical_performance_score))
    if ev is None:
        weighted = (
            (confidence_ratio * 0.45)
            + ((score_value / 100.0) * 0.40)
            + (market_quality_score * 0.15)
        )
    else:
        ev_score = max(0.0, min(1.0, ev / 0.10))
        weighted = (
            (ev_score * 0.35)
            + (confidence_ratio * 0.30)
            + ((score_value / 100.0) * 0.25)
            + (historical * 0.10)
        )
    score = int(round((weighted * 100.0) + decision_boost))
    return max(0, min(100, score))


def _decision_meta(status: str) -> dict[str, str]:
    mapping = {
        "ENTER_NOW": {
            "label": "ENTRADA APROVADA",
            "emoji": "🟢",
            "color": "#10b981",
            "action_label": "ENTRAR AGORA",
            "code": "enter_now",
            "css_class": "enter",
        },
        "WAIT_CONFIRMATION": {
            "label": "AGUARDAR CONFIRMAÇÃO",
            "emoji": "🟡",
            "color": "#f59e0b",
            "action_label": "AGUARDAR CONFIRMAÇÃO",
            "code": "wait_confirmation",
            "css_class": "wait",
        },
        "MONITOR_ONLY": {
            "label": "APENAS MONITORAR",
            "emoji": "🔵",
            "color": "#3b82f6",
            "action_label": "APENAS MONITORAR",
            "code": "monitor_only",
            "css_class": "monitor",
        },
        "DO_NOT_ENTER": {
            "label": "NÃO ENTRAR",
            "emoji": "🔴",
            "color": "#ef4444",
            "action_label": "NÃO ENTRAR",
            "code": "do_not_enter",
            "css_class": "exit",
        },
        "NO_DATA": {
            "label": "SEM DADOS",
            "emoji": "⚪",
            "color": "#94a3b8",
            "action_label": "APENAS MONITORAR",
            "code": "no_data",
            "css_class": "hold",
        },
    }
    return mapping.get(status, mapping["MONITOR_ONLY"])


def _normalize_risk(risk_level: str, decision_status: str) -> str:
    if decision_status in {"DO_NOT_ENTER"}:
        return "Alto"
    if decision_status == "NO_DATA":
        return "Indefinido"
    normalized = risk_level.replace("Medio", "Médio").replace("medio", "médio")
    return normalized or "Médio"


def _risk_color(level: str) -> str:
    clean = str(level or "").lower()
    if "indef" in clean:
        return "#94a3b8"
    if "alto" in clean or "sem valor" in clean:
        return "#ef4444"
    if "médio" in clean or "medio" in clean:
        return "#f59e0b"
    return "#10b981"


def _is_high_risk(level: str) -> bool:
    clean = str(level or "").lower()
    return "alto" in clean or "sem valor" in clean


def _confidence_badge(value: float) -> str:
    if value < 55:
        return "🔴 Baixa"
    if value < 65:
        return "🟡 Média"
    if value < 75:
        return "🟢 Boa"
    return "🔥 Alta"


def _score_badge(value: int) -> str:
    if value < 50:
        return "🔴 Fraco"
    if value < 65:
        return "🟡 Médio"
    if value < 80:
        return "🟢 Bom"
    return "🔥 Forte"


def _ev_badge_label(ev: float | None) -> str:
    if ev is None:
        return "⚪ Não confirmado"
    pct = ev * 100.0
    if ev < 0:
        return f"{pct:+.1f}% 🔴 Negativo"
    if ev < 0.03:
        return f"{pct:+.1f}% ⚪ Sem valor"
    if ev < 0.05:
        return f"{pct:+.1f}% 🟡 Quase"
    if ev < 0.08:
        return f"{pct:+.1f}% 🟢 Valor"
    return f"{pct:+.1f}% 🔥 Forte"


def _odd_badge_label(odd: float | None, profile: dict[str, float]) -> str:
    if odd is None or odd <= 1:
        return "- ⚪ Sem odd"
    if odd < profile["odd_min"]:
        return f"{odd:.2f} 🔴 Baixa demais"
    if odd > profile["odd_max"]:
        return f"{odd:.2f} 🔴 Alta demais"
    return f"{odd:.2f} 🟢 Operacional"


def _check_item(ok_label: str, passed: bool, fail_label: str, neutral: bool = False) -> dict[str, Any]:
    if neutral:
        return {"state": "neutral", "passed": None, "text": f"⚪ {ok_label if 'EV' not in ok_label else 'EV não confirmado'}"}
    return {
        "state": "positive" if passed else "negative",
        "passed": bool(passed),
        "text": f"{'✅' if passed else '❌'} {ok_label if passed else fail_label}",
    }


def _profile_label(profile: str) -> str:
    return {
        "conservador": "Conservador",
        "moderado": "Moderado",
        "agressivo": "Agressivo",
    }.get(profile, "Moderado")


def _minute_is_dangerous(minute: int) -> bool:
    if minute <= 0:
        return True
    return minute < 15 or 35 <= minute <= 45 or minute >= 75


def _fallback_risk(score: int, confidence_pct: float) -> str:
    if score < 55 or confidence_pct < 55:
        return "Alto"
    if score < 65 or confidence_pct < 65:
        return "Médio/Alto"
    return "Médio"


def _merge_reason(primary: str, secondary: str) -> str:
    secondary = secondary.strip()
    if not secondary:
        return primary
    if secondary.lower().startswith(primary.lower()):
        return secondary
    return f"{primary} {secondary}"


def _resolve_confidence_ratio(match: dict[str, Any]) -> float:
    score = _safe_float(match.get("confidence_score"), None)
    if score is not None and score <= 1.0:
        return max(0.0, min(1.0, score))
    pct = _safe_float(match.get("confidence_score_pct"), None)
    if pct is not None and pct > 0:
        return max(0.0, min(1.0, pct / 100.0))
    raw = _safe_float(match.get("confidence"), 0.0)
    if raw > 1.0:
        return max(0.0, min(1.0, raw / 100.0))
    return max(0.0, min(1.0, raw))


def _resolve_score(match: dict[str, Any]) -> int:
    for key in ("final_score", "entry_score", "score"):
        value = _safe_float(match.get(key), None)
        if value is not None:
            return max(0, min(100, int(round(value))))
    return 0


def _split_match(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    for token in (" x ", " vs ", " v ", " X "):
        if token in text:
            left, right = text.split(token, 1)
            return left.strip() or "-", right.strip() or "-"
    return text or "-", "-"


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
