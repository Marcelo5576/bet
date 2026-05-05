from __future__ import annotations

from typing import Any


class BaseGlobalAgent:
    name = "BaseAgent"
    context_type = "global"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class DataQualityAgent(BaseGlobalAgent):
    name = "DataQualityAgent"
    context_type = "data_quality"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        completeness = float(context.get("data_quality", 0.0))
        decision = "OK" if completeness >= 60 else "LOW_DATA"
        return {"agent_name": self.name, "decision": decision, "trust_score": round(max(0.2, min(0.95, completeness / 100)), 4), "reason": f"Qualidade dos dados em {completeness:.1f}/100."}


class StatsAgent(BaseGlobalAgent):
    name = "StatsAgent"
    context_type = "stats"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        probability = float(context.get("estimated_probability", 0.5))
        decision = "LEAN_IN" if probability >= 0.58 else "HOLD"
        return {"agent_name": self.name, "decision": decision, "trust_score": round(max(0.25, probability), 4), "reason": "Leitura estatística do ensemble e do baseline de forma."}


class OddsAgent(BaseGlobalAgent):
    name = "OddsAgent"
    context_type = "odds"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        ev = float(context.get("expected_value", 0.0) or 0.0)
        decision = "VALUE" if ev > 0.03 else "PRICE_BAD"
        return {"agent_name": self.name, "decision": decision, "trust_score": round(min(0.95, max(0.2, 0.5 + ev * 4)), 4), "reason": f"Edge calculado em {ev:.4f}."}


class ValueAgent(BaseGlobalAgent):
    name = "ValueAgent"
    context_type = "value"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        ev = float(context.get("expected_value", 0.0) or 0.0)
        confidence = float(context.get("confidence_score", 0.0) or 0.0)
        decision = "ENTRA_FORTE" if ev >= 0.08 and confidence >= 70 else "ENTRA_LEVE" if ev >= 0.03 and confidence >= 60 else "NO_BET"
        trust = min(0.96, max(0.15, (confidence / 100) * (1 + max(ev, 0))))
        return {"agent_name": self.name, "decision": decision, "trust_score": round(trust, 4), "reason": "Combinação de EV e confiança."}


class RiskAgent(BaseGlobalAgent):
    name = "RiskAgent"
    context_type = "risk"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        risk = float(context.get("risk_score", 50.0) or 50.0)
        decision = "SAI" if risk >= 75 else "SEGURA" if risk >= 55 else "OK"
        return {"agent_name": self.name, "decision": decision, "trust_score": round(max(0.2, min(0.9, 1 - (risk / 120))), 4), "reason": f"Risco agregado em {risk:.1f}/100."}


class PatternAgent(BaseGlobalAgent):
    name = "PatternAgent"
    context_type = "patterns"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        patterns = context.get("patterns") or []
        decision = "PATTERN_OK" if patterns else "NO_PATTERN"
        trust = 0.7 if patterns else 0.35
        return {"agent_name": self.name, "decision": decision, "trust_score": trust, "reason": "Consulta padrões históricos similares no RAG e nos insights."}


class DriftAgent(BaseGlobalAgent):
    name = "DriftAgent"
    context_type = "drift"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        drift = float(context.get("drift_score", 0.0) or 0.0)
        decision = "DRIFT_ALERT" if drift >= 0.45 else "STABLE"
        return {"agent_name": self.name, "decision": decision, "trust_score": round(max(0.2, 1 - drift), 4), "reason": f"Drift estimado em {drift:.3f}."}


class StrategyAgent(BaseGlobalAgent):
    name = "StrategyAgent"
    context_type = "strategy"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        edge = float(context.get("expected_value", 0.0) or 0.0)
        decision = "TEST_DRAFT" if edge >= 0.08 else "KEEP_BASELINE"
        return {"agent_name": self.name, "decision": decision, "trust_score": 0.62 if edge >= 0.08 else 0.48, "reason": "Avalia se vale propor rascunho de nova estratégia."}


class EvaluationAgent(BaseGlobalAgent):
    name = "EvaluationAgent"
    context_type = "evaluation"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        recent_roi = float(context.get("recent_roi", 0.0) or 0.0)
        decision = "GOOD" if recent_roi >= 0 else "BAD"
        return {"agent_name": self.name, "decision": decision, "trust_score": 0.65 if recent_roi >= 0 else 0.35, "reason": f"ROI recente em {recent_roi:.2f}%."}


class ExplainabilityAgent(BaseGlobalAgent):
    name = "ExplainabilityAgent"
    context_type = "explainability"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"agent_name": self.name, "decision": "EXPLAINED", "trust_score": 0.75, "reason": "A decisão foi acompanhada por evidências, agentes e contexto histórico."}


class SupervisorAgent(BaseGlobalAgent):
    name = "SupervisorAgent"
    context_type = "supervisor"

    def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        consensus = str(context.get("consensus_hint") or "NO_BET")
        return {"agent_name": self.name, "decision": consensus, "trust_score": 0.8, "reason": "Supervisão humana simulada: nenhuma mudança é ativada sem aprovação."}

