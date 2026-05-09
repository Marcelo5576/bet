from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WEIGHTS = {
    "ev": 25,
    "confidence": 15,
    "odd_quality": 10,
    "data_quality": 10,
    "pressure": 10,
    "league_history": 10,
    "market_history": 10,
    "risk": 10,
}


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_percent(value: Any, default: float = 0.0) -> float:
    raw = _safe_float(value, default)
    if raw is None:
        return default
    if 0 <= raw <= 1:
        raw *= 100.0
    return max(0.0, min(100.0, raw))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class ApexScorePayload:
    apex_score: float
    grade: str
    decision: str
    reasons: list[str]
    blockers: list[str]
    components: dict[str, float]
    odds_confirmed: bool
    data_quality: float
    risk_score: float


class ApexScoreService:
    def score_signal(
        self,
        signal: dict[str, Any],
        *,
        pressure_payload: dict[str, Any] | None = None,
        performance_ranking: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pressure_payload = pressure_payload or {}
        performance_ranking = performance_ranking or {}

        ev = _safe_float(signal.get("expected_value"), 0.0) or 0.0
        confidence = _normalize_percent(signal.get("confidence_score"), 0.0)
        data_quality = _normalize_percent(signal.get("data_quality"), 0.0)
        odd = _safe_float(signal.get("entry_odds") or signal.get("target_odds") or signal.get("odds"), 0.0) or 0.0
        odds_confirmed = odd > 1.0 and not bool(signal.get("mock")) and str(signal.get("source") or "").lower() != "mock"

        pressure_index = _safe_float(pressure_payload.get("pressure_index"), None)
        if pressure_index is None:
            pressure_index = _normalize_percent(pressure_payload.get("attacking_momentum"), 35.0)
        league_history = _normalize_percent(
            signal.get("league_reliability_score")
            or (signal.get("historical_context") or {}).get("league_reliability_score")
            or (performance_ranking.get("league") or {}).get("score")
            or 45.0
        )
        market_history = _normalize_percent(
            signal.get("historical_market_fit_score")
            or (signal.get("historical_context") or {}).get("market_fit_score")
            or (performance_ranking.get("market") or {}).get("score")
            or 45.0
        )
        risk_score = self._risk_score(signal, performance_ranking)

        components = {
            "ev": self._ev_score(ev),
            "confidence": confidence,
            "odd_quality": self._odd_quality_score(signal, odd, odds_confirmed),
            "data_quality": data_quality,
            "pressure": _clamp(pressure_index),
            "league_history": league_history,
            "market_history": market_history,
            "risk": risk_score,
        }
        weighted = sum((components[name] / 100.0) * weight for name, weight in WEIGHTS.items())
        apex_score = round(_clamp(weighted, 0.0, 100.0), 1)

        reasons: list[str] = []
        blockers: list[str] = []

        if components["ev"] >= 75:
            reasons.append("EV acima da faixa mínima operacional.")
        elif ev > 0:
            reasons.append("EV positivo, mas ainda sem folga confortável.")
        else:
            blockers.append("EV zerado ou negativo.")

        if confidence >= 80:
            reasons.append("Confiança estatística forte.")
        elif confidence < 60:
            blockers.append("Confiança abaixo do corte operacional.")

        if data_quality >= 80:
            reasons.append("Dados ao vivo e contexto vieram com boa qualidade.")
        elif data_quality < 50:
            blockers.append("Dados insuficientes para leitura madura.")

        if league_history >= 70:
            reasons.append("Histórico da liga reforça o cenário.")
        elif league_history < 40:
            blockers.append("Liga com histórico fraco ou instável.")

        if market_history >= 70:
            reasons.append("Mercado tem aderência histórica boa.")
        elif market_history < 40:
            blockers.append("Mercado ainda sem confirmação histórica suficiente.")

        if risk_score >= 75:
            reasons.append("Risco agregado ainda controlado.")
        elif risk_score < 45:
            blockers.append("Risco alto para o perfil atual.")

        if not odds_confirmed:
            blockers.append("Odds reais não confirmadas.")

        if str((performance_ranking.get("league") or {}).get("classification") or "").startswith("evitar"):
            blockers.append("Liga marcada como evitar pelo ranking operacional.")
        if str((performance_ranking.get("market") or {}).get("classification") or "").startswith("ruim"):
            blockers.append("Mercado ruim pelo ranking operacional.")

        decision = self._decision_for_score(apex_score, odds_confirmed=odds_confirmed, blockers=blockers, data_quality=data_quality)
        grade = self._grade(apex_score)

        payload = ApexScorePayload(
            apex_score=apex_score,
            grade=grade,
            decision=decision,
            reasons=reasons[:6],
            blockers=blockers[:8],
            components={name: round(value, 1) for name, value in components.items()},
            odds_confirmed=odds_confirmed,
            data_quality=round(data_quality, 1),
            risk_score=round(risk_score, 1),
        )
        return {
            "apex_score": payload.apex_score,
            "grade": payload.grade,
            "decision": payload.decision,
            "reasons": payload.reasons,
            "blockers": payload.blockers,
            "components": payload.components,
            "odds_confirmed": payload.odds_confirmed,
            "data_quality": payload.data_quality,
            "risk_score": payload.risk_score,
        }

    def _ev_score(self, ev: float) -> float:
        if ev <= 0:
            return 0.0
        if ev >= 0.25:
            return 100.0
        return _clamp((ev / 0.25) * 100.0)

    def _odd_quality_score(self, signal: dict[str, Any], odd: float, odds_confirmed: bool) -> float:
        if not odds_confirmed:
            return 0.0
        score = 55.0
        if 1.6 <= odd <= 3.0:
            score += 25.0
        elif 1.35 <= odd <= 4.0:
            score += 12.0
        liquidity_warning = bool(signal.get("liquidity_warning"))
        if liquidity_warning:
            score -= 25.0
        source = str(signal.get("source") or signal.get("provider") or "").lower()
        if source in {"api-football", "odds_api_io", "the_odds_api", "isports"}:
            score += 10.0
        return _clamp(score)

    def _risk_score(self, signal: dict[str, Any], performance_ranking: dict[str, Any]) -> float:
        risk_level = str(signal.get("risk_level") or "").strip().lower()
        if "alto" in risk_level:
            base = 20.0
        elif "medio" in risk_level:
            base = 60.0
        elif "baixo" in risk_level:
            base = 85.0
        else:
            base = 55.0
        if bool((performance_ranking.get("league") or {}).get("blocked")):
            base -= 25.0
        if bool((performance_ranking.get("market") or {}).get("blocked")):
            base -= 20.0
        return _clamp(base)

    def _decision_for_score(self, apex_score: float, *, odds_confirmed: bool, blockers: list[str], data_quality: float) -> str:
        if not odds_confirmed:
            return "REJECT"
        if data_quality < 45:
            return "NO_DATA"
        if blockers and apex_score < 65:
            return "REJECT"
        if apex_score >= 80:
            return "ENTER_NOW"
        if apex_score >= 65:
            return "WAIT"
        if apex_score >= 50:
            return "MONITOR"
        return "REJECT"

    def _grade(self, apex_score: float) -> str:
        if apex_score >= 90:
            return "A+"
        if apex_score >= 80:
            return "A"
        if apex_score >= 70:
            return "B"
        if apex_score >= 60:
            return "C"
        return "D"


_SERVICE = ApexScoreService()


def get_apex_score_service() -> ApexScoreService:
    return _SERVICE
