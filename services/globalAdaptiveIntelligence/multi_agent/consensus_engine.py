from __future__ import annotations

from typing import Any


class ConsensusEngine:
    def decide(self, outputs: list[dict[str, Any]]) -> dict[str, Any]:
        if not outputs:
            return {"final_decision": "NO_BET", "trust_score": 0.0, "reasons": []}
        score_by_decision: dict[str, float] = {}
        reasons: list[str] = []
        for row in outputs:
            decision = str(row.get("decision") or "NO_BET")
            score = float(row.get("trust_score", 0.0) or 0.0)
            score_by_decision[decision] = score_by_decision.get(decision, 0.0) + score
            reasons.append(f"{row.get('agent_name')}: {row.get('reason')}")
        final_decision, final_score = max(score_by_decision.items(), key=lambda item: item[1])
        total = sum(score_by_decision.values()) or 1.0
        return {
            "final_decision": final_decision,
            "trust_score": round(final_score / total, 4),
            "reasons": reasons,
        }

