from __future__ import annotations


def market_recommendations(signal: dict) -> list[dict]:
    game = signal.get("game", {})
    markets = game.get("markets") or {}
    recommendations = [_one_x_two(signal)]
    recommendations.append(_goals(signal, markets.get("goals") or {}))
    recommendations.append(_corners(signal, markets.get("corners") or {}))
    recommendations.extend(_corners_periods(signal, markets.get("corners") or {}))
    recommendations.append(_asian(signal, markets.get("asian") or {}))
    recommendations.append(_cards(signal, markets.get("cards") or {}))
    return recommendations


def _one_x_two(signal: dict) -> dict:
    selection = signal.get("team", "-")
    return {
        "market": "1X2",
        "selection": selection,
        "line": "-",
        "odds": signal.get("target_odds"),
        "action": signal.get("action", "AGUARDAR"),
        "entry": f"Entrar em vitoria de {selection}",
        "reason": "mercado principal calculado pela leitura de pressao, finalizacoes e odds.",
    }


def _goals(signal: dict, goals: dict) -> dict:
    game = signal.get("game", {})
    minute = int(game.get("minute") or 0)
    total_goals = int(game.get("home_goals") or 0) + int(game.get("away_goals") or 0)
    confidence = int(signal.get("confidence") or 0)
    over = goals.get("over")
    under = goals.get("under")
    if not over and not under:
        return _missing("Gols")
    if confidence >= 65 and total_goals == 0 and minute <= 70 and over:
        return _rec("Gols", "Over", over, "AGUARDAR", "pressao favorece gol, mas confirmar ritmo antes de entrar.")
    if minute >= 70 and total_goals == 0 and under:
        return _rec("Gols", "Under", under, "AGUARDAR", "minuto avancado sem gol favorece protecao no under.")
    return _rec("Gols", "Over" if over else "Under", over or under, "AGUARDAR", "mercado disponivel, mas sem gatilho forte.")


def _corners(signal: dict, corners: dict) -> dict:
    confidence = int(signal.get("confidence") or 0)
    over = corners.get("over")
    under = corners.get("under")
    if not over and not under:
        return _missing("Escanteios")
    if confidence >= 65 and over:
        return _rec("Escanteios", "Over", over, "AGUARDAR", "pressao ofensiva pode favorecer cantos.")
    return _rec("Escanteios", "Over" if over else "Under", over or under, "AGUARDAR", "sem pressao suficiente para entrada imediata.")


def _corners_periods(signal: dict, corners: dict) -> list[dict]:
    game = signal.get("game", {})
    minute = int(game.get("minute") or 0)
    rows: list[dict] = []
    first_half = corners.get("first_half") or {}
    second_half = corners.get("second_half") or {}
    if first_half:
        action = "AGUARDAR" if minute <= 45 else "SEM DADOS"
        reason = "linha de escanteios do 1T disponivel no feed ao vivo." if minute <= 45 else "1T encerrado; usar apenas como referencia historica do jogo."
        rows.append(_rec("Escanteios 1T", "Over" if first_half.get("over") else "Under", first_half.get("over") or first_half.get("under"), action, reason))
    if second_half:
        action = "AGUARDAR" if minute >= 46 else "SEM DADOS"
        reason = "linha de escanteios do 2T disponivel para acompanhamento em tempo real." if minute >= 46 else "mercado do 2T ja listado, mas o jogo ainda nao entrou no segundo tempo."
        rows.append(_rec("Escanteios 2T", "Over" if second_half.get("over") else "Under", second_half.get("over") or second_half.get("under"), action, reason))
    return rows


def _asian(signal: dict, asian: dict) -> dict:
    team = signal.get("team")
    game = signal.get("game", {})
    home = game.get("home")
    side = "home" if team == home else "away"
    item = asian.get(side)
    if not item:
        return _missing("Asiatica/Handicap")
    action = "AGUARDAR" if signal.get("action") != "ENTRAR" else "ENTRAR"
    return _rec("Asiatica/Handicap", team or side, item, action, "handicap alinhado ao time alvo da leitura.")


def _cards(signal: dict, cards: dict) -> dict:
    over = cards.get("over")
    under = cards.get("under")
    if not over and not under:
        return _missing("Cartoes")
    return _rec("Cartoes", "Over" if over else "Under", over or under, "AGUARDAR", "mercado auxiliar de disciplina disponivel na fonte atual.")


def _missing(market: str) -> dict:
    return {
        "market": market,
        "selection": "Sem dados",
        "line": "-",
        "odds": None,
        "action": "SEM DADOS",
        "entry": f"Nao entrar em {market}: odds indisponiveis",
        "reason": "a fonte atual nao entregou odds desse mercado.",
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


def _entry_text(market: str, selection: str, line: str, odds) -> str:
    odd_text = odds if odds is not None else "-"
    if market == "Gols":
        return f"Entrar em Gols {selection} {line} na odd {odd_text}"
    if market == "Escanteios":
        return f"Entrar em Escanteios {selection} {line} na odd {odd_text}"
    if market == "Asiatica/Handicap":
        return f"Entrar em Handicap Asiatico {selection} {line} na odd {odd_text}"
    return f"Entrar em {market} {selection} {line} na odd {odd_text}"
