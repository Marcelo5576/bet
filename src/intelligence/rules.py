from __future__ import annotations

from dataclasses import asdict

from src.providers.base import LiveGame


def analyze_game(
    game: LiveGame,
    min_confidence: int,
    bankroll: float = 1000,
    unit_percent: float = 1,
    max_stake_units: float = 2,
) -> dict:
    leader = "home" if game.home_pressure >= game.away_pressure else "away"
    pressure_delta = abs(game.home_pressure - game.away_pressure)
    shots_delta = abs(game.home_shots_on - game.away_shots_on)
    total_goals = game.home_goals + game.away_goals

    confidence = 45 + min(30, pressure_delta // 2) + min(15, shots_delta * 4)
    if 15 <= game.minute <= 70:
        confidence += 8
    if total_goals == 0:
        confidence += 6
    if game.minute > 80:
        confidence -= 12

    confidence = max(0, min(100, confidence))
    team = game.home if leader == "home" else game.away
    market = f"1X2 - {team}"
    target_odds = game.odds_home if leader == "home" else game.odds_away
    value = _value_read(confidence, target_odds)
    data_quality = _data_quality(game)
    action = _action(confidence, min_confidence)
    action = _adjust_action_for_value(action, value)
    stake_units = _stake_units(confidence, min_confidence, max_stake_units)
    unit_value = bankroll * (unit_percent / 100)
    if action not in {"ENTRAR", "AGUARDAR"}:
        stake_units = 0

    return {
        "game": asdict(game),
        "action": action,
        "team": team,
        "market": market,
        "confidence": confidence,
        "target_odds": target_odds,
        "estimated_probability": value["estimated_probability"],
        "implied_probability": value["implied_probability"],
        "value_edge": value["edge"],
        "fair_odds": value["fair_odds"],
        "data_quality": data_quality["score"],
        "data_quality_notes": data_quality["notes"],
        "stake_units": stake_units,
        "stake_value": round(unit_value * stake_units, 2),
        "manual_only": True,
        "risk_note": "Alerta informativo. Entrada somente com confirmacao manual.",
        "reason": _reason(game, team, pressure_delta, shots_delta, confidence),
    }


def best_signal(
    games: list[LiveGame],
    min_confidence: int,
    bankroll: float = 1000,
    unit_percent: float = 1,
    max_stake_units: float = 2,
) -> dict | None:
    if not games:
        return None
    viable_games = [
        game for game in games
        if 10 <= game.minute <= 78 and not _is_esports_game(game)
    ]
    signals = [
        analyze_game(game, min_confidence, bankroll, unit_percent, max_stake_units)
        for game in viable_games
    ]
    signals = [signal for signal in signals if signal["action"] in {"ENTRAR", "AGUARDAR"}]
    if not signals:
        return None
    signals.sort(
        key=lambda item: (
            item["game"].get("priority", 50),
            -item["confidence"],
        )
    )
    return signals[0]


def ranked_signals(
    games: list[LiveGame],
    min_confidence: int,
    bankroll: float = 1000,
    unit_percent: float = 1,
    max_stake_units: float = 2,
) -> list[dict]:
    viable_games = [
        game for game in games
        if _is_viable_scan_game(game) and not _is_esports_game(game)
    ]
    signals = [
        analyze_game(game, min_confidence, bankroll, unit_percent, max_stake_units)
        for game in viable_games
    ]
    signals.sort(
        key=lambda item: (
            item["game"].get("priority", 50),
            -item["confidence"],
        )
    )
    return signals


def _is_viable_scan_game(game: LiveGame) -> bool:
    if 10 <= game.minute <= 78:
        return True
    return 0 <= game.minute <= 9


def _is_esports_game(game: LiveGame) -> bool:
    text = " ".join(
        [
            game.league,
            game.division,
            game.home,
            game.away,
        ]
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


def position_management(signal: dict) -> dict:
    if not signal.get("entered"):
        return {
            "decision": "OBSERVAR",
            "reason": "jogo escolhido, mas voce ainda nao marcou que entrou.",
        }

    action = signal.get("action")
    confidence = int(signal.get("confidence") or 0)
    edge = signal.get("value_edge")
    game = signal.get("game", {})
    minute = int(game.get("minute") or 0)
    total_goals = int(game.get("home_goals") or 0) + int(game.get("away_goals") or 0)

    if action == "SAIR" or confidence < 45:
        decision = "SAIR"
        reason = "sinal perdeu forca ou risco ficou alto."
    elif minute >= 78 and total_goals == 0:
        decision = "PROTEGER / SAIR PARCIAL"
        reason = "minuto avancado sem confirmacao no placar."
    elif edge is not None and edge < 0:
        decision = "PROTEGER"
        reason = "edge ficou negativo contra a odd atual."
    elif action in {"ENTRAR", "AGUARDAR"} and confidence >= 65:
        decision = "MANTER"
        reason = "leitura ainda sustenta a posicao."
    else:
        decision = "AGUARDAR"
        reason = "a leitura esta neutra; evite aumentar exposicao."

    return {"decision": decision, "reason": reason}


def _action(confidence: int, min_confidence: int) -> str:
    if confidence >= min_confidence + 15:
        return "ENTRAR"
    if confidence >= min_confidence:
        return "AGUARDAR"
    if confidence >= 45:
        return "SEGURAR"
    return "SAIR"


def _adjust_action_for_value(action: str, value: dict[str, float | None]) -> str:
    edge = value.get("edge")
    if edge is None:
        return action
    if edge < -0.05 and action == "ENTRAR":
        return "AGUARDAR"
    if edge < -0.10:
        return "SEGURAR"
    return action


def _stake_units(confidence: int, min_confidence: int, max_stake_units: float) -> float:
    if confidence < min_confidence:
        return 0
    if confidence >= min_confidence + 20:
        return max_stake_units
    if confidence >= min_confidence + 10:
        return min(max_stake_units, 1.5)
    return min(max_stake_units, 1)


def _value_read(confidence: int, odds: float | None) -> dict[str, float | None]:
    estimated_probability = round(min(0.85, max(0.05, confidence / 100)), 3)
    fair_odds = round(1 / estimated_probability, 2)
    if not odds or odds <= 1:
        return {
            "estimated_probability": estimated_probability,
            "implied_probability": None,
            "edge": None,
            "fair_odds": fair_odds,
        }
    implied_probability = round(1 / odds, 3)
    return {
        "estimated_probability": estimated_probability,
        "implied_probability": implied_probability,
        "edge": round(estimated_probability - implied_probability, 3),
        "fair_odds": fair_odds,
    }


def _data_quality(game: LiveGame) -> dict:
    score = 40
    notes = []
    if 10 <= game.minute <= 78:
        score += 15
    else:
        notes.append("minuto fora da janela ideal")
    if game.home_shots_on + game.away_shots_on > 0:
        score += 15
    else:
        notes.append("sem finalizacoes no alvo")
    if game.home_pressure or game.away_pressure:
        score += 15
    else:
        notes.append("sem leitura de pressao")
    if game.odds_home or game.odds_draw or game.odds_away:
        score += 10
    else:
        notes.append("sem odds 1X2")
    if game.markets.get("goals") or game.markets.get("asian") or game.markets.get("corners"):
        score += 5
    else:
        notes.append("poucos mercados alternativos")
    return {"score": min(100, score), "notes": notes or ["dados suficientes"]}


def _reason(
    game: LiveGame,
    team: str,
    pressure_delta: int,
    shots_delta: int,
    confidence: int,
) -> str:
    return (
        f"{team} tem vantagem de pressao ({pressure_delta}) e diferenca de "
        f"finalizacoes no alvo ({shots_delta}). Placar {game.home_goals}x"
        f"{game.away_goals} aos {game.minute} minutos. Confianca {confidence}%."
    )
