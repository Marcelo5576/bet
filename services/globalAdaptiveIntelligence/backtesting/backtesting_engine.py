from __future__ import annotations

from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill


class GlobalBacktestingEngine:
    def __init__(self):
        self.skill = get_football_quant_ai_skill()

    def runBacktest(self, **kwargs) -> dict[str, Any]:
        return self.skill.backtesting.runBacktest(
            kwargs["request"]
        ).__dict__

    def compareStrategies(self, summaries: list[dict[str, Any]]) -> dict[str, Any]:
        ranked = sorted(summaries, key=lambda item: (float(item.get("roi", 0.0)), float(item.get("hit_rate", 0.0))), reverse=True)
        return {
            "best": ranked[0] if ranked else {},
            "worst": ranked[-1] if ranked else {},
            "count": len(ranked),
        }

    def calculateBrierScore(self, probabilities: list[float], outcomes: list[int]) -> float:
        if not probabilities or len(probabilities) != len(outcomes):
            return 0.0
        return round(sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities), 6)

    def calculateLogLoss(self, probabilities: list[float], outcomes: list[int]) -> float:
        import math

        if not probabilities or len(probabilities) != len(outcomes):
            return 0.0
        eps = 1e-9
        total = 0.0
        for p, y in zip(probabilities, outcomes):
            p = min(1 - eps, max(eps, p))
            total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        return round(total / len(probabilities), 6)

