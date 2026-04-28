from __future__ import annotations

from typing import Any

from src.intelligence.markets import market_recommendations


def paper_opportunities(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signal in signals:
        game = signal.get("game") or {}
        for rec in market_recommendations(signal):
            rows.append(_row(signal, rec, game))
        rows.extend(_half_markets(signal, game))

    rows.sort(
        key=lambda item: (
            item["rank"],
            -float(item.get("score") or 0),
            -float(item.get("confidence") or 0),
        )
    )
    return rows[:24]


def best_paper_entry(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    opportunities = paper_opportunities(signals)
    return opportunities[0] if opportunities else None


def _row(signal: dict[str, Any], rec: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    action = rec.get("action") or signal.get("action") or "AGUARDAR"
    odds = rec.get("odds")
    rank = _rank(action, odds)
    score = _paper_score(signal, rec)
    risk = _paper_risk(signal, score)
    return {
        "game_id": game.get("game_id"),
        "match": f"{game.get('home', '-')} x {game.get('away', '-')}",
        "scoreline": f"{game.get('home_goals', 0)}x{game.get('away_goals', 0)}",
        "minute": game.get("minute", "-"),
        "league": game.get("division") or game.get("league") or "-",
        "market": rec.get("market") or signal.get("market") or "-",
        "selection": rec.get("selection") or signal.get("team") or "-",
        "line": rec.get("line") or "-",
        "odds": odds,
        "action": action,
        "entry": rec.get("entry") or "-",
        "reason": rec.get("reason") or signal.get("reason") or "-",
        "confidence": int(signal.get("confidence") or 0),
        "score": score,
        "risk": risk,
        "rank": rank,
    }


def _half_markets(signal: dict[str, Any], game: dict[str, Any]) -> list[dict[str, Any]]:
    minute = int(game.get("minute") or 0)
    confidence = int(signal.get("confidence") or 0)
    total_goals = int(game.get("home_goals") or 0) + int(game.get("away_goals") or 0)
    pressure_delta = abs(int(game.get("home_pressure") or 0) - int(game.get("away_pressure") or 0))
    target = signal.get("team") or "-"

    rows = []
    if 10 <= minute <= 44:
        action = "ENTRAR" if confidence >= 78 and total_goals == 0 else "AGUARDAR"
        rows.append(
            {
                **_base_half(signal, game),
                "market": "Gols 1T",
                "selection": "Over 0.5 1T",
                "line": "0.5",
                "odds": None,
                "action": action,
                "entry": "Simular Over 0.5 gols no 1o tempo se a casa oferecer odd minima aceitavel",
                "reason": "janela de primeiro tempo com pressao e placar ainda aberto.",
                "rank": _rank(action, None) + 1,
            }
        )
    if 45 <= minute <= 78:
        action = "ENTRAR" if confidence >= 75 and pressure_delta >= 18 else "AGUARDAR"
        rows.append(
            {
                **_base_half(signal, game),
                "market": "Gols 2T",
                "selection": "Over 0.5/1.5 2T",
                "line": "0.5 ou 1.5",
                "odds": None,
                "action": action,
                "entry": "Simular gol no 2o tempo; escolher linha conforme odd disponivel",
                "reason": "segundo tempo com leitura de pressao sustentada.",
                "rank": _rank(action, None) + 1,
            }
        )
    if 45 <= minute <= 78:
        rows.append(
            {
                **_base_half(signal, game),
                "market": "Vencedor 2T",
                "selection": target,
                "line": "2T",
                "odds": None,
                "action": "AGUARDAR",
                "entry": f"Simular {target} para vencer o 2o tempo apenas se houver odd com valor",
                "reason": "mercado dependente de odds especificas que a fonte atual pode nao entregar.",
                "rank": _rank("AGUARDAR", None) + 2,
            }
        )
    return rows


def _base_half(signal: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    score = _paper_score(signal, None)
    risk = _paper_risk(signal, score)
    return {
        "game_id": game.get("game_id"),
        "match": f"{game.get('home', '-')} x {game.get('away', '-')}",
        "scoreline": f"{game.get('home_goals', 0)}x{game.get('away_goals', 0)}",
        "minute": game.get("minute", "-"),
        "league": game.get("division") or game.get("league") or "-",
        "confidence": int(signal.get("confidence") or 0),
        "score": score,
        "risk": risk,
    }


def _rank(action: str, odds: Any) -> int:
    if action == "ENTRAR" and odds:
        return 0
    if action == "ENTRAR":
        return 1
    if action == "AGUARDAR" and odds:
        return 2
    if action == "AGUARDAR":
        return 3
    return 9


def _paper_score(signal: dict[str, Any], rec: dict[str, Any] | None) -> int:
    saved = int(signal.get("entry_score") or 0)
    if saved > 0:
        return max(1, min(100, saved))

    confidence = float(signal.get("confidence") or 0)
    quality = float(signal.get("data_quality") or 0)
    edge = signal.get("value_edge")
    score = confidence * 0.55 + quality * 0.25
    if edge is None:
        score += 6
    else:
        score += max(-12, min(18, float(edge) * 160))

    action = str((rec or {}).get("action") or signal.get("action") or "").upper()
    if action == "ENTRAR":
        score += 8
    elif action == "AGUARDAR":
        score -= 4
    elif action == "SEM DADOS":
        score = min(score, 28)
    elif action in {"SAIR", "SEGURAR"}:
        score = min(score, 40)

    if rec and rec.get("odds"):
        score += 4
    return max(1, min(100, int(round(score))))


def _paper_risk(signal: dict[str, Any], score: int) -> int:
    saved = int(signal.get("risk_score") or 0)
    if saved and saved < 100:
        return max(1, min(100, saved))
    return max(1, min(100, 100 - score))
