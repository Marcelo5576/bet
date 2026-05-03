from __future__ import annotations


def market_recommendations(signal: dict) -> list[dict]:
    game = signal.get("game", {})
    markets = game.get("markets") or {}
    context = _context(signal)
    recommendations = [_one_x_two(signal, context)]
    recommendations.append(_goals(signal, markets.get("goals") or {}, context))
    recommendations.append(_corners(signal, markets.get("corners") or {}, context))
    recommendations.extend(_corners_periods(signal, markets.get("corners") or {}, context))
    recommendations.append(_asian(signal, markets.get("asian") or {}, context))
    recommendations.append(_cards(signal, markets.get("cards") or {}, context))
    return recommendations


def _context(signal: dict) -> dict:
    game = signal.get("game", {})
    markets = game.get("markets") or {}
    home_pressure = _safe_int(game.get("home_pressure"))
    away_pressure = _safe_int(game.get("away_pressure"))
    home_shots_on = _safe_int(game.get("home_shots_on"))
    away_shots_on = _safe_int(game.get("away_shots_on"))
    home_goals = _safe_int(game.get("home_goals"))
    away_goals = _safe_int(game.get("away_goals"))
    minute = _safe_int(game.get("minute"))
    corners_live = ((markets.get("corners") or {}).get("live") or {})
    home_corners = _safe_int(corners_live.get("home"))
    away_corners = _safe_int(corners_live.get("away"))
    total_corners = _safe_int(corners_live.get("total"), home_corners + away_corners)
    total_shots_on = home_shots_on + away_shots_on
    total_goals = home_goals + away_goals
    pressure_gap = abs(home_pressure - away_pressure)
    shots_gap = abs(home_shots_on - away_shots_on)
    pressure_total = home_pressure + away_pressure
    pressure_peak = max(home_pressure, away_pressure)
    leading_side = "home" if home_pressure >= away_pressure else "away"
    confidence = _safe_int(signal.get("confidence"))
    entry_score = _safe_int(signal.get("entry_score"))
    risk_score = _safe_int(signal.get("risk_score"), 50)
    edge = _safe_float(signal.get("value_edge"))
    line_bias = 0
    if confidence >= 72:
        line_bias += 1
    if entry_score >= 68:
        line_bias += 1
    if risk_score <= 42:
        line_bias += 1
    if edge >= 0.07:
        line_bias += 1
    if edge <= -0.03:
        line_bias -= 1
    return {
        "minute": minute,
        "confidence": confidence,
        "entry_score": entry_score,
        "risk_score": risk_score,
        "edge": edge,
        "home_pressure": home_pressure,
        "away_pressure": away_pressure,
        "pressure_gap": pressure_gap,
        "pressure_total": pressure_total,
        "pressure_peak": pressure_peak,
        "home_shots_on": home_shots_on,
        "away_shots_on": away_shots_on,
        "shots_gap": shots_gap,
        "total_shots_on": total_shots_on,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "total_goals": total_goals,
        "home_corners": home_corners,
        "away_corners": away_corners,
        "total_corners": total_corners,
        "leading_side": leading_side,
        "line_bias": line_bias,
    }


def _one_x_two(signal: dict, ctx: dict) -> dict:
    selection = signal.get("team", "-")
    action = str(signal.get("action") or "AGUARDAR").upper()
    confidence = ctx["confidence"]
    edge = ctx["edge"]
    if action == "ENTRAR" and confidence >= 67 and edge >= 0:
        reason = (
            f"{selection} domina a leitura com pressão {ctx['pressure_gap']} e vantagem de chutes {ctx['shots_gap']}. "
            f"Odd ainda conversa com o edge de {_pct(edge)}."
        )
        return _rec("1X2", selection, {"line": "-", "odds": signal.get("target_odds")}, "ENTRAR", reason)
    if confidence >= 60:
        reason = (
            f"{selection} segue com o lado forte da partida, mas ainda vale esperar mais confirmação de ritmo ou preço. "
            f"Pressão {ctx['pressure_gap']} · chutes no alvo {ctx['total_shots_on']}."
        )
        return _rec("1X2", selection, {"line": "-", "odds": signal.get("target_odds")}, "AGUARDAR", reason)
    return _rec(
        "1X2",
        selection,
        {"line": "-", "odds": signal.get("target_odds")},
        "AGUARDAR",
        "Mercado principal aberto, mas a superioridade ainda não está limpa o bastante para compra agora.",
    )


def _goals(signal: dict, goals: dict, ctx: dict) -> dict:
    over = goals.get("over")
    under = goals.get("under")
    if not over and not under:
        return _missing("Gols")

    minute = ctx["minute"]
    total_goals = ctx["total_goals"]
    total_shots = ctx["total_shots_on"]
    peak_pressure = ctx["pressure_peak"]
    line_bias = ctx["line_bias"]

    over_line = _line_value(over)
    under_line = _line_value(under)

    over_hot = (
        over
        and minute >= 12
        and minute <= 78
        and total_shots >= 5
        and peak_pressure >= 63
        and line_bias >= 1
    )
    if over_hot:
        reason = (
            f"Jogo quente para gols: {total_shots} chutes no alvo, pico de pressão {peak_pressure} e linha {over_line or '-'} ainda jogável."
        )
        return _rec("Gols", "Over", over, "ENTRAR", reason)

    under_hot = (
        under
        and minute >= 62
        and total_goals <= 1
        and total_shots <= 4
        and peak_pressure <= 57
    )
    if under_hot:
        reason = (
            f"Ritmo travado para gols: minuto {minute}', só {total_shots} chutes no alvo e pressão controlada. Under {under_line or '-'} fica protegido."
        )
        return _rec("Gols", "Under", under, "ENTRAR", reason)

    if over and minute <= 60 and peak_pressure >= 58:
        reason = (
            f"Mercado de gols ficou vivo com pressão {peak_pressure} e {total_shots} chutes no alvo, mas ainda pede mais um empurrão antes da entrada."
        )
        return _rec("Gols", "Over", over, "AGUARDAR", reason)

    if under and minute >= 70 and total_goals == 0:
        return _rec(
            "Gols",
            "Under",
            under,
            "AGUARDAR",
            f"Minuto {minute}' sem gol mantém viés de under, mas o preço ainda precisa respeitar a trava do risco.",
        )

    fallback = over or under
    selection = "Over" if over else "Under"
    return _rec(
        "Gols",
        selection,
        fallback,
        "AGUARDAR",
        "Mercado de gols presente, mas sem combinação forte o bastante entre volume, tempo e linha para cravar entrada agora.",
    )


def _corners(signal: dict, corners: dict, ctx: dict) -> dict:
    over = corners.get("over")
    under = corners.get("under")
    if not over and not under:
        return _missing("Escanteios")

    minute = ctx["minute"]
    total_corners = ctx["total_corners"]
    pressure_total = ctx["pressure_total"]
    total_shots = ctx["total_shots_on"]

    over_line = _line_value(over)
    under_line = _line_value(under)

    if over and over_line is not None:
        corners_gap = over_line - total_corners
        over_hot = (
            minute >= 10
            and minute <= 78
            and pressure_total >= 92
            and total_shots >= 4
            and corners_gap <= 4.5
        )
        if over_hot:
            reason = (
                f"Cantos com volume real: {total_corners} já batidos, pressão somada {pressure_total} e linha over {over_line} ao alcance."
            )
            return _rec("Escanteios", "Over", over, "ENTRAR", reason)

    if under and under_line is not None:
        under_hot = (
            minute >= 68
            and total_corners <= max(2, under_line - 2)
            and pressure_total <= 84
        )
        if under_hot:
            reason = (
                f"Cantos em ritmo baixo: só {total_corners} até agora, pressão total {pressure_total} e linha under {under_line} ainda tem gordura."
            )
            return _rec("Escanteios", "Under", under, "ENTRAR", reason)

    if over:
        return _rec(
            "Escanteios",
            "Over",
            over,
            "AGUARDAR",
            f"Mercado de cantos está montado, com {total_corners} ao vivo. Falta só mais aceleração antes de entrar no over.",
        )
    return _rec(
        "Escanteios",
        "Under",
        under,
        "AGUARDAR",
        f"Under disponível em cantos, mas o jogo ainda não desacelerou o suficiente para antecipar a venda da linha.",
    )


def _corners_periods(signal: dict, corners: dict, ctx: dict) -> list[dict]:
    minute = ctx["minute"]
    pressure_total = ctx["pressure_total"]
    total_corners = ctx["total_corners"]
    rows: list[dict] = []
    first_half = corners.get("first_half") or {}
    second_half = corners.get("second_half") or {}

    fh_item = first_half.get("over") or first_half.get("under")
    if fh_item:
        fh_over = first_half.get("over")
        if minute <= 45 and fh_over and pressure_total >= 88 and total_corners <= max(5, (_line_value(fh_over) or 0) + 1):
            rows.append(
                _rec(
                    "Escanteios 1T",
                    "Over",
                    fh_over,
                    "ENTRAR",
                    f"1T ainda vivo para cantos: pressão somada {pressure_total} e linha curta no mercado do primeiro tempo.",
                )
            )
        else:
            rows.append(
                _rec(
                    "Escanteios 1T",
                    "Over" if fh_over else "Under",
                    fh_item,
                    "AGUARDAR" if minute <= 45 else "SEM DADOS",
                    "Mercado de escanteios do 1T aberto; usar só enquanto o primeiro tempo estiver em jogo.",
                )
            )

    sh_item = second_half.get("over") or second_half.get("under")
    if sh_item:
        sh_over = second_half.get("over")
        if minute >= 46 and sh_over and pressure_total >= 90:
            rows.append(
                _rec(
                    "Escanteios 2T",
                    "Over",
                    sh_over,
                    "ENTRAR",
                    f"2T voltou acelerado para cantos: pressão total {pressure_total} sustenta compra de linha no segundo tempo.",
                )
            )
        else:
            rows.append(
                _rec(
                    "Escanteios 2T",
                    "Over" if sh_over else "Under",
                    sh_item,
                    "AGUARDAR" if minute >= 46 else "SEM DADOS",
                    "Mercado de escanteios do 2T pronto; aguarde o jogo reabrir ritmo para ativar entrada.",
                )
            )
    return rows


def _asian(signal: dict, asian: dict, ctx: dict) -> dict:
    team = signal.get("team")
    game = signal.get("game", {})
    home = game.get("home")
    side = "home" if team == home else "away"
    item = asian.get(side)
    if not item:
        return _missing("Asiatica/Handicap")

    if str(signal.get("action") or "").upper() == "ENTRAR" and ctx["confidence"] >= 66:
        return _rec(
            "Asiatica/Handicap",
            team or side,
            item,
            "ENTRAR",
            f"Linha asiática acompanha o lado mais forte da leitura, com confiança {ctx['confidence']}% e risco {ctx['risk_score']}/100.",
        )

    return _rec(
        "Asiatica/Handicap",
        team or side,
        item,
        "AGUARDAR",
        "Handicap está alinhado ao lado dominante, mas ainda vale esperar o próximo ajuste de preço ou mais confirmação de pressão.",
    )


def _cards(signal: dict, cards: dict, ctx: dict) -> dict:
    over = cards.get("over")
    under = cards.get("under")
    if not over and not under:
        return _missing("Cartoes")

    minute = ctx["minute"]
    pressure_total = ctx["pressure_total"]
    total_goals = ctx["total_goals"]
    over_line = _line_value(over)

    if over and minute >= 55 and pressure_total >= 92:
        reason = (
            f"Jogo tenso para disciplina: pressão total {pressure_total}, minuto {minute}' e mercado de cartões ainda precificado em {over_line or '-'}."
        )
        return _rec("Cartoes", "Over", over, "ENTRAR", reason)

    if under and minute >= 70 and total_goals <= 1 and pressure_total <= 82:
        return _rec(
            "Cartoes",
            "Under",
            under,
            "ENTRAR",
            f"Partida controlada para cartões: minuto {minute}', poucos gatilhos emocionais e ritmo contido.",
        )

    return _rec(
        "Cartoes",
        "Over" if over else "Under",
        over or under,
        "AGUARDAR",
        "Mercado disciplinar disponível, mas ainda sem tensão de jogo suficiente para assumir posição com folga.",
    )


def _missing(market: str) -> dict:
    return {
        "market": market,
        "selection": "Sem dados",
        "line": "-",
        "odds": None,
        "action": "SEM DADOS",
        "entry": f"Sem entrada em {market}",
        "reason": "A fonte atual nao entregou odds desse mercado.",
    }


def _rec(market: str, selection: str, item: dict, action: str, reason: str) -> dict:
    line = item.get("line") or "-"
    odds = item.get("odds")
    return {
        "market": market,
        "selection": selection,
        "line": line,
        "odds": odds,
        "action": action,
        "entry": _entry_text(market, selection, line, odds),
        "reason": reason,
    }


def _line_value(item: dict | None) -> float | None:
    if not isinstance(item, dict):
        return None
    raw = str(item.get("line") or "").strip()
    if not raw:
        return None
    if raw[0].lower() in {"o", "u"} and len(raw) > 1:
        raw = raw[1:]
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{round(value * 100, 1)}%"


def _entry_text(market: str, selection: str, line: str, odds) -> str:
    odd_text = f"@ {odds}" if odds is not None else ""
    clean_line = _clean_line(selection, line)
    pretty = _pretty_selection(selection)
    if market == "1X2":
        return _compact_text(selection, "para vencer", odd_text)
    if market == "Gols":
        return _totals_text(pretty, clean_line, "gols", odd_text)
    if market == "Escanteios":
        return _totals_text(pretty, clean_line, "escanteios", odd_text)
    if market == "Escanteios 1T":
        return _totals_text(pretty, clean_line, "escanteios 1T", odd_text)
    if market == "Escanteios 2T":
        return _totals_text(pretty, clean_line, "escanteios 2T", odd_text)
    if market == "Asiatica/Handicap":
        return _compact_text(selection, "AH", clean_line, odd_text)
    if market == "Cartoes":
        return _totals_text(pretty, clean_line, "cartoes", odd_text)
    return _compact_text(market, pretty, clean_line, odd_text)


def _pretty_selection(selection: str) -> str:
    value = str(selection or "").strip()
    if value.lower() == "over":
        return "Mais de"
    if value.lower() == "under":
        return "Menos de"
    return value or "-"


def _clean_line(selection: str, line: str) -> str:
    value = str(line or "").strip()
    if not value or value == "-":
        return ""
    lower_selection = str(selection or "").strip().lower()
    if lower_selection == "over" and value[:1].lower() == "o":
        return value[1:]
    if lower_selection == "under" and value[:1].lower() == "u":
        return value[1:]
    return value


def _compact_text(*parts: str) -> str:
    return " ".join(part for part in parts if str(part or "").strip())


def _totals_text(prefix: str, line: str, subject: str, odd_text: str) -> str:
    if prefix in {"Mais de", "Menos de"} and line:
        return _compact_text(prefix, line, subject, odd_text)
    return _compact_text(prefix, subject, line, odd_text)
