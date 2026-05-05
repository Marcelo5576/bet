from __future__ import annotations

from random import Random
from statistics import median
from typing import Any


class MonteCarloEngine:
    def __init__(self, *, seed: int = 20260503):
        self.random = Random(seed)

    def run(self, *, hit_rate: float, average_odd: float, bankroll: float, stake_pct: float, paths: int, steps: int) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        ruin_count = 0
        finals: list[float] = []
        for path_index in range(paths):
            current = bankroll
            peak = bankroll
            max_drawdown = 0.0
            for _ in range(steps):
                stake = max(0.0, current * stake_pct)
                win = self.random.random() < hit_rate
                pnl = stake * (average_odd - 1) if win else -stake
                current = round(current + pnl, 2)
                peak = max(peak, current)
                max_drawdown = max(max_drawdown, peak - current)
                if current <= bankroll * 0.1:
                    ruin_count += 1
                    break
            finals.append(current)
            rows.append({"path_index": path_index, "final_bankroll": current, "max_drawdown": round(max_drawdown, 2)})
        ordered = sorted(finals)
        return {
            "paths": paths,
            "steps": steps,
            "ruin_risk": round(ruin_count / max(1, paths), 4),
            "median_final_bankroll": round(median(finals), 2) if finals else bankroll,
            "p10_final_bankroll": round(ordered[max(0, int(len(ordered) * 0.1) - 1)], 2) if ordered else bankroll,
            "p90_final_bankroll": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 2) if ordered else bankroll,
            "results": rows,
        }

