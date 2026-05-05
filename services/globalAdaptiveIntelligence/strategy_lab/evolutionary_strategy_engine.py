from __future__ import annotations

from random import Random
from typing import Any

from ..repository import GlobalAdaptiveRepository


class EvolutionaryStrategyEngine:
    def __init__(self, repository: GlobalAdaptiveRepository, *, seed: int = 20260503):
        self.repository = repository
        self.random = Random(seed)

    def mutation(self, genome: dict[str, Any]) -> dict[str, Any]:
        mutated = dict(genome)
        mutated["ev_min"] = round(max(0.0, float(mutated.get("ev_min", 0.03)) + self.random.uniform(-0.01, 0.02)), 4)
        mutated["confidence_min"] = round(max(45.0, float(mutated.get("confidence_min", 60)) + self.random.uniform(-5, 5)), 2)
        return mutated

    def crossover(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        return {
            "ev_min": round((float(left.get("ev_min", 0.03)) + float(right.get("ev_min", 0.03))) / 2, 4),
            "confidence_min": round((float(left.get("confidence_min", 60)) + float(right.get("confidence_min", 60))) / 2, 2),
        }

    def fitnessScore(self, *, roi: float, drawdown: float, total_entries: int, risk_score: float, complexity: float) -> float:
        return round((roi * 0.5) - (drawdown * 0.2) + (min(total_entries, 200) * 0.05) - (risk_score * 0.15) - (complexity * 0.1), 4)

    def evolve(self, *, base_genome: dict[str, Any], generations: int = 3, user_id: int | None = None) -> dict[str, Any]:
        population: list[dict[str, Any]] = []
        current = dict(base_genome)
        for generation in range(1, generations + 1):
            candidate = self.mutation(current)
            fitness = self.fitnessScore(
                roi=self.random.uniform(1, 9),
                drawdown=self.random.uniform(1, 8),
                total_entries=self.random.randint(20, 120),
                risk_score=self.random.uniform(12, 48),
                complexity=2,
            )
            population.append({"generation": generation, "genome": candidate, "fitness_score": fitness})
            current = candidate
        self.repository.save_strategy_population(population, user_id=user_id)
        return {"population": population, "best": max(population, key=lambda item: item["fitness_score"]) if population else {}}

