from __future__ import annotations

from collections import defaultdict
from typing import Any


def summarize_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [item for item in history if item.get("outcome") in {"win", "loss"}]
    if not settled:
        return {
            "sample_size": 0,
            "brier_score": None,
            "profit_units": 0,
            "roi_units": 0,
            "fast_learning": _fast_learning([]),
            "message": "Sem historico fechado ainda. Use cautela e stake minima.",
        }

    return {
        "sample_size": len(settled),
        "overall": _rate(settled),
        "brier_score": _brier_score(settled),
        "profit_units": _profit_units(settled),
        "roi_units": _roi_units(settled),
        "by_league": _top_rates(settled, "league"),
        "by_team": _top_rates(settled, "team"),
        "by_market": _top_rates(settled, "market"),
        "by_action": _top_rates(settled, "action"),
        "by_confidence_bucket": _confidence_buckets(settled),
        "backtest": _backtest(settled),
        "recent_form": [item.get("outcome") for item in settled[:10]],
        "fast_learning": _fast_learning(settled),
    }


def summarize_history_with_simulation(
    history: list[dict[str, Any]],
    simulation_sessions: list[dict[str, Any]] | None,
    *,
    simulation_weight: float = 0.35,
    max_simulation_rows: int = 240,
) -> dict[str, Any]:
    base = summarize_history(history)
    sessions = [item for item in (simulation_sessions or []) if isinstance(item, dict)]
    if not sessions:
        base["real_sample_size"] = int(base.get("sample_size") or 0)
        base["simulation_sample_size"] = 0
        base["simulation_weight"] = 0.0
        return base

    sim_records = simulation_rows_as_history(sessions, max_rows=max_simulation_rows)
    if not sim_records:
        base["real_sample_size"] = int(base.get("sample_size") or 0)
        base["simulation_sample_size"] = 0
        base["simulation_weight"] = 0.0
        return base

    weight = max(0.05, min(1.0, float(simulation_weight)))
    weighted_size = max(1, int(round(len(sim_records) * weight)))
    blended_records = list(history or []) + sim_records[:weighted_size]
    blended = summarize_history(blended_records)
    blended["real_sample_size"] = int(base.get("sample_size") or 0)
    blended["simulation_sample_size"] = weighted_size
    blended["simulation_weight"] = round(weight, 2)
    return blended


def simulation_rows_as_history(
    sessions: list[dict[str, Any]],
    *,
    max_rows: int = 240,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in sessions:
        created_at = str(session.get("created_at") or "")
        for row in session.get("rows") or []:
            if not isinstance(row, dict):
                continue
            outcome = _sim_outcome(row.get("outcome"))
            if outcome is None:
                continue
            match = str(row.get("match") or "")
            home, away = _split_match(match)
            odds = _safe_float(row.get("odds"))
            stake = max(0.0, _safe_float(row.get("stake")))
            confidence = max(1, min(99, _safe_int(row.get("win_prob_pct"))))
            probability = round(confidence / 100.0, 3)
            rows.append(
                {
                    "signal_id": f"sim-{_safe_int(row.get('idx'))}-{created_at}",
                    "created_at": created_at,
                    "finished_at": created_at,
                    "outcome": outcome,
                    "profit_units": _safe_float(row.get("profit")),
                    "stake_units": stake,
                    "target_odds": odds if odds > 1 else None,
                    "estimated_probability": probability,
                    "confidence": confidence,
                    "entry_market": str(row.get("market") or "Simulacao"),
                    "market": str(row.get("market") or "Simulacao"),
                    "team": str(row.get("selection") or ""),
                    "game": {
                        "home": home,
                        "away": away,
                        "league": str(session.get("scan_scope") or "Simulacao"),
                        "division": "Simulacao",
                    },
                }
            )
            if len(rows) >= max_rows:
                return rows
    return rows


def _rate(items: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for item in items if item.get("outcome") == "win")
    losses = sum(1 for item in items if item.get("outcome") == "loss")
    total = wins + losses
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "hit_rate": round((wins / total) * 100, 1) if total else 0,
    }


def _top_rates(items: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if field == "league":
            key = item.get("game", {}).get("league")
        elif field == "team":
            game = item.get("game", {})
            for team in (game.get("home"), game.get("away")):
                if team:
                    groups[str(team)].append(item)
            continue
        elif field == "market":
            key = item.get("entry_market") or item.get("market")
        else:
            key = item.get(field)
        groups[str(key or "Sem valor")].append(item)

    ranked = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        ranked.append({
            "name": key,
            **_rate(group),
            "profit_units": _profit_units(group),
            "roi_units": _roi_units(group),
        })
    ranked.sort(
        key=lambda item: (item["profit_units"], item["hit_rate"], item["total"]),
        reverse=True,
    )
    return ranked[:5]


def _fast_learning(items: list[dict[str, Any]]) -> dict[str, Any]:
    recent_5 = items[:5]
    recent_10 = items[:10]
    momentum = _momentum_score(recent_10)
    recent_5_rate = _rate(recent_5)
    recent_10_rate = _rate(recent_10)
    return {
        "mode": _fast_mode(recent_5_rate, recent_10_rate, momentum),
        "momentum_score": momentum,
        "recent_5": recent_5_rate,
        "recent_10": recent_10_rate,
        "hot_markets": _fast_groups(items, "market", positive=True),
        "cold_markets": _fast_groups(items, "market", positive=False),
        "hot_teams": _fast_groups(items, "team", positive=True),
        "cold_teams": _fast_groups(items, "team", positive=False),
        "hot_leagues": _fast_groups(items, "league", positive=True),
        "cold_leagues": _fast_groups(items, "league", positive=False),
    }


def _fast_mode(
    recent_5_rate: dict[str, Any],
    recent_10_rate: dict[str, Any],
    momentum: int,
) -> str:
    if recent_5_rate["total"] >= 4 and recent_5_rate["losses"] >= 3:
        return "defensivo"
    if recent_10_rate["total"] >= 7 and recent_10_rate["hit_rate"] < 40:
        return "defensivo"
    if momentum <= 35:
        return "defensivo"
    if recent_5_rate["total"] >= 4 and recent_5_rate["hit_rate"] >= 65 and momentum >= 62:
        return "oportunista"
    return "neutro"


def _momentum_score(items: list[dict[str, Any]]) -> int:
    if not items:
        return 50
    weights = [2.2, 1.9, 1.6, 1.35, 1.15, 1, 0.85, 0.7, 0.6, 0.5]
    score = 50.0
    for item, weight in zip(items, weights):
        score += 7 * weight if item.get("outcome") == "win" else -7 * weight
    return int(max(0, min(100, round(score))))


def _fast_groups(
    items: list[dict[str, Any]],
    field: str,
    *,
    positive: bool,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items[:30]:
        for key in _group_keys(item, field):
            groups[key].append(item)

    ranked = []
    for key, group in groups.items():
        rate = _rate(group)
        profit = _profit_units(group)
        roi = _roi_units(group)
        confidence = "baixa" if rate["total"] < 3 else "media" if rate["total"] < 8 else "alta"
        is_positive = profit > 0 or (rate["total"] >= 2 and rate["hit_rate"] >= 60)
        is_negative = profit < 0 or (rate["total"] >= 2 and rate["hit_rate"] <= 40)
        if positive and not is_positive:
            continue
        if not positive and not is_negative:
            continue
        ranked.append({
            "name": key,
            **rate,
            "profit_units": profit,
            "roi_units": roi,
            "confidence": confidence,
        })

    if positive:
        ranked.sort(key=lambda item: (item["profit_units"], item["hit_rate"], item["total"]), reverse=True)
    else:
        ranked.sort(key=lambda item: (item["profit_units"], -item["losses"], item["hit_rate"]))
    return ranked[:6]


def _group_keys(item: dict[str, Any], field: str) -> list[str]:
    if field == "league":
        value = item.get("game", {}).get("league") or item.get("game", {}).get("division")
        return [str(value)] if value else []
    if field == "team":
        game = item.get("game", {})
        return [str(team) for team in (game.get("home"), game.get("away")) if team]
    if field == "market":
        value = item.get("entry_market") or item.get("market")
        return [str(value)] if value else []
    value = item.get(field)
    return [str(value)] if value else []


def _confidence_buckets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        confidence = int(item.get("confidence") or 0)
        bucket_start = (confidence // 10) * 10
        groups[f"{bucket_start}-{bucket_start + 9}"].append(item)
    ranked = [{"name": key, **_rate(group)} for key, group in groups.items()]
    ranked.sort(key=lambda item: item["name"])
    return ranked


def _brier_score(items: list[dict[str, Any]]) -> float | None:
    scored = []
    for item in items:
        probability = item.get("estimated_probability")
        if probability is None:
            continue
        actual = 1 if item.get("outcome") == "win" else 0
        scored.append((float(probability) - actual) ** 2)
    if not scored:
        return None
    return round(sum(scored) / len(scored), 4)


def _profit_units(items: list[dict[str, Any]]) -> float:
    total = 0.0
    for item in items:
        if item.get("profit_units") is not None:
            total += float(item.get("profit_units") or 0)
            continue
        stake = float(item.get("stake_units") or 0)
        odds = item.get("target_odds")
        if item.get("outcome") == "win" and odds:
            total += stake * (float(odds) - 1)
        elif item.get("outcome") == "loss":
            total -= stake
    return round(total, 2)


def _roi_units(items: list[dict[str, Any]]) -> float:
    staked = sum(float(item.get("stake_units") or 0) for item in items)
    if staked <= 0:
        return 0
    return round((_profit_units(items) / staked) * 100, 1)


def _backtest(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"trades": 0, "profit_units": 0, "max_drawdown_units": 0}
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    curve = []
    for item in reversed(items):
        profit = _profit_units([item])
        equity += profit
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        curve.append(round(equity, 2))
    return {
        "trades": len(items),
        "profit_units": round(equity, 2),
        "max_drawdown_units": round(abs(max_drawdown), 2),
        "last_equity_units": curve[-10:],
    }


def _sim_outcome(value: Any) -> str | None:
    label = str(value or "").strip().lower()
    if label == "green":
        return "win"
    if label == "red":
        return "loss"
    return None


def _split_match(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    for separator in (" x ", " vs ", " v ", " X ", " VS "):
        if separator in text:
            left, right = text.split(separator, 1)
            return left.strip(), right.strip()
    return text or "Time A", "Time B"


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
