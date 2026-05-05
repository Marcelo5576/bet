from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def apply_risk_controls(
    signal: dict,
    learning_context: dict,
    min_history_for_enter: int,
    min_edge_to_enter: float,
    kelly_fraction: float,
    unit_percent: float,
    max_stake_units: float,
    *,
    active_position: bool = False,
) -> dict:
    sample_size = int(learning_context.get("sample_size") or 0)
    edge = signal.get("value_edge")
    odds = signal.get("target_odds")
    fair_odds = signal.get("fair_odds")
    probability = signal.get("estimated_probability")
    data_quality = int(signal.get("data_quality") or 0)
    fast_learning = learning_context.get("fast_learning") or {}
    original_units = float(signal.get("stake_units") or 0)
    original_value = float(signal.get("stake_value") or 0)
    base_unit_value = original_value / original_units if original_units > 0 else 0
    minute = _safe_int((signal.get("game") or {}).get("minute"))
    red_cards = _red_cards(signal)
    reasons = []
    signal["entry_gate"] = "PASS"

    if not active_position:
        if minute < 15:
            if signal.get("action") == "ENTRAR":
                signal["action"] = "AGUARDAR"
            signal["stake_units"] = 0
            signal["entry_gate"] = "NO_BET"
            reasons.append("janela 0-15: jogo ainda cru para entrada")
        elif 35 <= minute <= 45:
            if signal.get("action") == "ENTRAR":
                signal["action"] = "AGUARDAR"
            signal["stake_units"] = 0
            signal["entry_gate"] = "NO_BET"
            reasons.append("janela 35-45: risco de intervalo e ruido tatico")
        elif minute >= 80:
            if signal.get("action") == "ENTRAR":
                signal["action"] = "AGUARDAR"
            signal["stake_units"] = 0
            signal["entry_gate"] = "NO_BET"
            reasons.append("80+: somente gestao, sem nova entrada")
        elif minute >= 75:
            if signal.get("action") == "ENTRAR":
                signal["action"] = "AGUARDAR"
            signal["stake_units"] = 0
            signal["entry_gate"] = "NO_BET"
            reasons.append("75+: evitar entrada nova; manter apenas leitura de gestao")
        elif 66 <= minute <= 75:
            reasons.append("66-75: janela de gestao obrigatoria")

    if sample_size < min_history_for_enter:
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append(
            f"historico insuficiente ({sample_size}/{min_history_for_enter})"
        )

    if edge is not None and edge < min_edge_to_enter:
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append(f"edge abaixo do minimo ({round(edge * 100, 1)}%)")

    if odds and fair_odds and float(odds) <= float(fair_odds):
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append(
            f"odd da casa sem valor frente a odd justa ({round(float(odds), 2)} <= {round(float(fair_odds), 2)})"
        )

    if data_quality < 70:
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append(f"qualidade dos dados baixa ({data_quality}/100)")

    if _is_fast_defensive(fast_learning):
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append("aprendizado rapido em modo defensivo")

    cold_match = _matching_cold_pattern(signal, fast_learning)
    if cold_match:
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "NO_BET"
        reasons.append(f"padrao recente negativo: {cold_match}")

    if red_cards > 0:
        if signal.get("action") == "ENTRAR":
            signal["action"] = "AGUARDAR"
        signal["stake_units"] = 0
        signal["entry_gate"] = "PAUSE"
        reasons.append("cartao vermelho detectado: pausar 5 minutos e recalcular pressao")

    if signal.get("action") == "ENTRAR" and odds and probability:
        kelly_units = _fractional_kelly_units(
            float(probability),
            float(odds),
            kelly_fraction,
            unit_percent,
            max_stake_units,
        )
        signal["stake_units"] = kelly_units
        reasons.append(f"stake por Kelly fracionado {kelly_fraction}x")

    if signal.get("action") != "ENTRAR":
        signal["stake_units"] = 0

    signal["stake_value"] = round(base_unit_value * float(signal.get("stake_units") or 0), 2)
    signal["risk_pause_minutes"] = 5 if red_cards > 0 else 0
    signal["minute_rule"] = _minute_rule_label(minute, active_position)

    note = "Entrada somente com confirmacao manual."
    if reasons:
        note += " Controle de risco: " + "; ".join(reasons) + "."
    signal["risk_note"] = note
    return signal


def apply_daily_red_stop(signal: dict, history: list[dict], daily_red_limit: int) -> dict:
    stop = red_stop_status(history, daily_red_limit)
    red_count = stop["red_count"]
    signal["daily_reds"] = red_count
    signal["daily_red_limit"] = daily_red_limit
    if not stop.get("discipline_alert"):
        return signal

    signal["risk_blocked"] = False
    warning = (
        "Alerta de disciplina: sequencia de reds acima do limite sugerido "
        f"({red_count}/{daily_red_limit}). Reduza exposicao e revise criterios."
    )
    current_note = str(signal.get("risk_note") or "Entrada somente com confirmacao manual.")
    if warning not in current_note:
        signal["risk_note"] = f"{current_note} {warning}"
    return signal


def today_red_count(history: list[dict]) -> int:
    return red_stop_status(history, 9999)["red_count"]


def red_stop_status(history: list[dict], daily_red_limit: int) -> dict:
    now = datetime.now(SAO_PAULO)
    window_start, unlock_at = _risk_window(now)
    total = 0
    for item in history or []:
        if item.get("outcome") != "loss":
            continue
        finished_at = item.get("finished_at") or item.get("created_at")
        parsed = _local_datetime(finished_at)
        if parsed and window_start <= parsed < unlock_at:
            total += 1
    return {
        "locked": False,
        "discipline_alert": total >= daily_red_limit and now < unlock_at,
        "red_count": total,
        "red_limit": daily_red_limit,
        "window_start": window_start,
        "unlock_at": unlock_at,
    }


def is_forbidden_esports_game(game: dict) -> bool:
    text = " ".join(
        str(game.get(key) or "")
        for key in ("league", "division", "home", "away")
    ).lower()
    forbidden = (
        "esoccer",
        "e-soccer",
        "e soccer",
        "esports",
        "e-sports",
        "efootball",
        "e-football",
        "cyber",
        "fifa",
        "pes ",
    )
    return any(term in text for term in forbidden)


def _local_date(value: str | None):
    parsed = _local_datetime(value)
    return parsed.date() if parsed else None


def _local_datetime(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SAO_PAULO)
    return parsed.astimezone(SAO_PAULO)


def _risk_window(now: datetime) -> tuple[datetime, datetime]:
    today_six = datetime.combine(now.date(), time(hour=6), tzinfo=SAO_PAULO)
    if now >= today_six:
        return today_six, today_six + timedelta(days=1)
    yesterday_six = today_six - timedelta(days=1)
    return yesterday_six, today_six


def _fractional_kelly_units(
    probability: float,
    odds: float,
    kelly_fraction: float,
    unit_percent: float,
    max_stake_units: float,
) -> float:
    b = odds - 1
    if b <= 0 or unit_percent <= 0:
        return 0
    q = 1 - probability
    full_kelly = ((probability * b) - q) / b
    if full_kelly <= 0:
        return 0
    bankroll_fraction = full_kelly * kelly_fraction
    units = bankroll_fraction / (unit_percent / 100)
    return round(max(0, min(max_stake_units, units)), 2)


def _is_fast_defensive(fast_learning: dict) -> bool:
    if fast_learning.get("mode") == "defensivo":
        return True
    recent_5 = fast_learning.get("recent_5") or {}
    return int(recent_5.get("total") or 0) >= 4 and int(recent_5.get("losses") or 0) >= 3


def _matching_cold_pattern(signal: dict, fast_learning: dict) -> str | None:
    terms = _signal_terms(signal)
    for key in ("cold_markets", "cold_teams", "cold_leagues"):
        for row in fast_learning.get(key) or []:
            if int(row.get("total") or 0) < 2:
                continue
            name = str(row.get("name") or "").strip().lower()
            if name and name in terms:
                return row.get("name")
    return None


def _red_cards(signal: dict) -> int:
    brain = signal.get("brain") if isinstance(signal.get("brain"), dict) else {}
    facts = brain.get("facts") if isinstance(brain.get("facts"), dict) else {}
    total = _safe_int(facts.get("red_home")) + _safe_int(facts.get("red_away"))
    if total:
        return total
    game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
    markets = game.get("markets") if isinstance(game.get("markets"), dict) else {}
    live_facts = markets.get("live_facts") if isinstance(markets.get("live_facts"), dict) else {}
    return _safe_int(live_facts.get("red_home")) + _safe_int(live_facts.get("red_away"))


def _minute_rule_label(minute: int, active_position: bool) -> str:
    if minute < 15:
        return "janela_crua"
    if minute <= 35:
        return "buscar_padrao"
    if minute <= 45:
        return "cuidado_intervalo"
    if minute <= 65:
        return "melhor_janela_live"
    if minute <= 75:
        return "gestao_obrigatoria"
    if minute < 80:
        return "evitar_nova_entrada" if not active_position else "gestao"
    return "somente_gestao"


def _safe_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _signal_terms(signal: dict) -> str:
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
