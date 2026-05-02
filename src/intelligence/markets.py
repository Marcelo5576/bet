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
        "entry": _entry_text("1X2", selection, "-", signal.get("target_odds")),
        "reason": "Leitura combina pressao, finalizacoes e preco do mercado.",
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
        return _rec("Gols", "Over", over, "AGUARDAR", "Pressao favorece gol, mas ainda pede confirmacao do ritmo.")
    if minute >= 70 and total_goals == 0 and under:
        return _rec("Gols", "Under", under, "AGUARDAR", "Minuto avancado sem gol sustenta protecao no under.")
    return _rec("Gols", "Over" if over else "Under", over or under, "AGUARDAR", "Linha aberta, mas sem vantagem clara agora.")


def _corners(signal: dict, corners: dict) -> dict:
    confidence = int(signal.get("confidence") or 0)
    over = corners.get("over")
    under = corners.get("under")
    if not over and not under:
        return _missing("Escanteios")
    if confidence >= 65 and over:
        return _rec("Escanteios", "Over", over, "AGUARDAR", "Volume ofensivo pode empurrar o over de cantos.")
    return _rec("Escanteios", "Over" if over else "Under", over or under, "AGUARDAR", "Linha aberta, mas sem aceleracao suficiente.")


def _corners_periods(signal: dict, corners: dict) -> list[dict]:
    game = signal.get("game", {})
    minute = int(game.get("minute") or 0)
    rows: list[dict] = []
    first_half = corners.get("first_half") or {}
    second_half = corners.get("second_half") or {}
    if first_half:
        action = "AGUARDAR" if minute <= 45 else "SEM DADOS"
        reason = "Linha de escanteios do 1T disponivel no ao vivo." if minute <= 45 else "1T encerrado; manter apenas como referencia."
        rows.append(_rec("Escanteios 1T", "Over" if first_half.get("over") else "Under", first_half.get("over") or first_half.get("under"), action, reason))
    if second_half:
        action = "AGUARDAR" if minute >= 46 else "SEM DADOS"
        reason = "Linha de escanteios do 2T pronta para leitura ao vivo." if minute >= 46 else "Mercado do 2T aberto, mas o jogo ainda nao voltou."
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
    return _rec("Asiatica/Handicap", team or side, item, action, "Linha acompanha o lado mais forte da leitura ao vivo.")


def _cards(signal: dict, cards: dict) -> dict:
    over = cards.get("over")
    under = cards.get("under")
    if not over and not under:
        return _missing("Cartoes")
    return _rec("Cartoes", "Over" if over else "Under", over or under, "AGUARDAR", "Mercado disciplinar disponivel, sem gatilho forte agora.")


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
