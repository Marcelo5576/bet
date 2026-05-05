from __future__ import annotations

from ..schemas import ValueBetAssessment


class ValueBetService:
    def assess(self, estimated_probability: float, offered_odd: float | None, confidence_score: float, *, require_complete_data: bool = True) -> ValueBetAssessment:
        if offered_odd is None or offered_odd <= 1:
            return ValueBetAssessment(
                offered_odd=offered_odd,
                estimated_probability=estimated_probability,
                implied_probability=None,
                fair_odd=None,
                expected_value=None,
                band="Sem valor",
                allowed=False,
                reason="Odd inválida ou ausente.",
            )
        implied = 1 / offered_odd
        fair_odd = (1 / estimated_probability) if estimated_probability > 0 else None
        ev = (estimated_probability * offered_odd) - 1
        if ev <= 0:
            band = "Sem valor"
        elif ev < 0.03:
            band = "Valor baixo"
        elif ev < 0.08:
            band = "Valor moderado"
        else:
            band = "Valor alto"
        allowed = ev > 0 and confidence_score >= 55 and (not require_complete_data or fair_odd is not None)
        reason = band
        if ev <= 0:
            reason = "EV <= 0, então a entrada não tem valor matemático."
        elif confidence_score < 55:
            reason = "Confiança baixa para sustentar a recomendação."
        return ValueBetAssessment(
            offered_odd=offered_odd,
            estimated_probability=round(estimated_probability, 4),
            implied_probability=round(implied, 4),
            fair_odd=round(fair_odd, 4) if fair_odd else None,
            expected_value=round(ev, 4),
            band=band,
            allowed=allowed,
            reason=reason,
        )

