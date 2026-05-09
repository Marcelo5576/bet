from __future__ import annotations

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_percent(value: Any, default: float = 0.0) -> float:
    raw = _safe_float(value, default)
    if 0 <= raw <= 1:
        raw *= 100.0
    return max(0.0, min(100.0, raw))


class BaseAgent:
    name = "BaseAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _result(
        self,
        *,
        status: str,
        score: float,
        findings: list[str] | None = None,
        blockers: list[str] | None = None,
        recommendation: str = "MONITOR",
    ) -> dict[str, Any]:
        return {
            "agent_name": self.name,
            "status": status,
            "score": round(max(0.0, min(100.0, score)), 1),
            "findings": findings or [],
            "blockers": blockers or [],
            "recommendation": recommendation,
        }


class DataQualityAgent(BaseAgent):
    name = "DataQualityAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        quality = _normalize_percent(signal.get("data_quality"), 0.0)
        if quality < 45:
            return self._result(status="blocked", score=quality, blockers=["Dados insuficientes."], recommendation="NO_DATA")
        findings = ["Dados vivos com cobertura suficiente."] if quality >= 80 else ["Cobertura parcial; manter leitura cautelosa."]
        return self._result(status="ok" if quality >= 70 else "warning", score=quality, findings=findings, recommendation="ENTER_NOW" if quality >= 80 else "WAIT")


class OddsAgent(BaseAgent):
    name = "OddsAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        odd = _safe_float(signal.get("entry_odds") or signal.get("target_odds") or signal.get("odds"), 0.0)
        confirmed = bool(signal.get("apex_odds_confirmed")) or odd > 1.0
        if not confirmed:
            return self._result(status="blocked", score=0.0, blockers=["Odd real não confirmada."], recommendation="REJECT")
        score = 90.0 if 1.6 <= odd <= 3.0 else 68.0 if 1.35 <= odd <= 4.0 else 52.0
        return self._result(status="ok" if score >= 75 else "warning", score=score, findings=[f"Odd operacional em {odd:.2f}."], recommendation="ENTER_NOW" if score >= 75 else "WAIT")


class PressureAgent(BaseAgent):
    name = "PressureAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        payload = context.get("pressure") or {}
        if payload.get("data_insufficient"):
            return self._result(status="warning", score=35.0, blockers=["Sem pressão/momentum confiáveis."], recommendation="MONITOR")
        score = _normalize_percent(payload.get("pressure_index"), 50.0)
        findings = [f"Momentum {payload.get('attacking_momentum')} · intensidade {payload.get('intensity')}."]
        return self._result(status="ok" if score >= 65 else "warning", score=score, findings=findings, recommendation="ENTER_NOW" if score >= 78 else "WAIT" if score >= 60 else "MONITOR")


class RiskAgent(BaseAgent):
    name = "RiskAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        level = str(signal.get("risk_level") or "").lower()
        score = 82.0 if "baixo" in level else 60.0 if "medio" in level else 22.0 if "alto" in level else 55.0
        if score < 40:
            return self._result(status="blocked", score=score, blockers=["Risco alto para o perfil atual."], recommendation="REJECT")
        return self._result(status="ok" if score >= 70 else "warning", score=score, findings=[f"Risco atual: {signal.get('risk_level') or 'indefinido'}."], recommendation="WAIT" if score < 70 else "ENTER_NOW")


class BacktestAgent(BaseAgent):
    name = "BacktestAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        market = context.get("ranking", {}).get("market") or {}
        league = context.get("ranking", {}).get("league") or {}
        score = max(_safe_float(market.get("score"), 45.0), _safe_float(league.get("score"), 45.0))
        blockers = []
        if bool(market.get("blocked")):
            blockers.append("Mercado ruim no histórico/backtest.")
        if bool(league.get("blocked")):
            blockers.append("Liga degradada no histórico/backtest.")
        recommendation = "REJECT" if blockers else "ENTER_NOW" if score >= 75 else "WAIT" if score >= 60 else "MONITOR"
        return self._result(status="blocked" if blockers else "ok" if score >= 70 else "warning", score=score, findings=[f"Ranking mercado {market.get('classification', 'neutro')} / liga {league.get('classification', 'observacao')}."], blockers=blockers, recommendation=recommendation)


class MarketAgent(BaseAgent):
    name = "MarketAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        recommendations = signal.get("market_recommendations") or []
        confirmed = any(bool(item.get("confirmed")) and str(item.get("action") or "").upper() == "ENTRAR" for item in recommendations if isinstance(item, dict))
        supported = bool(recommendations)
        if not supported:
            return self._result(status="blocked", score=25.0, blockers=["Mercado não suportado ou sem oferta real."], recommendation="NO_DATA")
        score = 84.0 if confirmed else 58.0
        findings = ["Mercado suportado e com linha confirmada."] if confirmed else ["Mercado suportado, mas ainda sem linha clara."]
        return self._result(status="ok" if confirmed else "warning", score=score, findings=findings, recommendation="ENTER_NOW" if confirmed else "WAIT")


class TelegramAgent(BaseAgent):
    name = "TelegramAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        vip = context.get("telegram_vip") or {}
        if not vip:
            return self._result(status="warning", score=40.0, findings=["Canal VIP ainda não avaliado."], recommendation="MONITOR")
        if vip.get("eligible"):
            return self._result(status="ok", score=92.0, findings=["Sinal elegível para canal VIP filtrado."], recommendation="ENTER_NOW")
        blockers = [str(item) for item in vip.get("blockers") or []]
        return self._result(status="warning" if blockers else "ok", score=55.0 if blockers else 70.0, findings=[str(item) for item in vip.get("reasons") or []], blockers=blockers, recommendation="WAIT" if blockers else "ENTER_NOW")


class SupervisorAgent(BaseAgent):
    name = "SupervisorAgent"

    def evaluate(self, signal: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        results = context.get("agents") or []
        blockers = []
        findings = []
        recommendation_votes: dict[str, int] = {}
        weighted = 0.0
        for item in results:
            blockers.extend(item.get("blockers") or [])
            findings.extend(item.get("findings") or [])
            recommendation = str(item.get("recommendation") or "MONITOR")
            recommendation_votes[recommendation] = recommendation_votes.get(recommendation, 0) + 1
            weighted += _safe_float(item.get("score"))
        average = weighted / max(1, len(results))
        if any("Odd real" in block for block in blockers):
            decision = "REJECT"
        elif any("Dados insuficientes" in block for block in blockers):
            decision = "NO_DATA"
        elif any("Risco alto" in block for block in blockers):
            decision = "REJECT"
        elif average >= 80 and bool(signal.get("entry_allowed")) and _safe_float(signal.get("apex_score")) >= 80:
            decision = "ENTER_NOW"
        elif average >= 65:
            decision = "WAIT"
        elif average >= 50:
            decision = "MONITOR"
        else:
            decision = "REJECT"
        return self._result(
            status="ok" if decision == "ENTER_NOW" else "warning" if decision in {"WAIT", "MONITOR"} else "blocked",
            score=average,
            findings=findings[:10],
            blockers=blockers[:10],
            recommendation=decision,
        )


def run_supervisor_pipeline(signal: dict[str, Any], *, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    agents = [
        DataQualityAgent(),
        OddsAgent(),
        PressureAgent(),
        RiskAgent(),
        BacktestAgent(),
        MarketAgent(),
        TelegramAgent(),
    ]
    outputs = [agent.evaluate(signal, context) for agent in agents]
    supervisor = SupervisorAgent().evaluate(signal, {**context, "agents": outputs})
    return {
        "agents": outputs,
        "supervisor": supervisor,
        "decision": supervisor["recommendation"],
        "score": supervisor["score"],
        "findings": supervisor["findings"],
        "blockers": supervisor["blockers"],
    }
