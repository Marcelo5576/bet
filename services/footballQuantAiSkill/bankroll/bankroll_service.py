from __future__ import annotations

from ..schemas import BankrollAdvice


PROFILE_MULTIPLIERS = {
    "conservador": 0.25,
    "moderado": 0.50,
    "agressivo": 0.75,
}


class BankrollService:
    def recommend(self, bankroll: float, probability: float, odd: float | None, profile: str = "moderado") -> BankrollAdvice:
        profile_key = (profile or "moderado").strip().lower()
        profile_multiplier = PROFILE_MULTIPLIERS.get(profile_key, 0.50)
        max_cap = round(bankroll * 0.03, 2)
        if odd is None or odd <= 1:
            return BankrollAdvice(bankroll, profile_key, profile_multiplier, 0.0, 0.0, max_cap, 0.0, False, "Odd inválida para Kelly.")
        if probability <= 0:
            return BankrollAdvice(bankroll, profile_key, profile_multiplier, 0.0, 0.0, max_cap, 0.0, False, "Probabilidade inválida.")
        b = odd - 1
        kelly = (((odd - 1) * probability) - (1 - probability)) / b if b else 0.0
        if kelly <= 0:
            return BankrollAdvice(bankroll, profile_key, profile_multiplier, 0.0, 0.0, max_cap, round(kelly, 4), False, "Kelly <= 0, sem stake.")
        stake = bankroll * kelly * profile_multiplier
        stake = min(max_cap, round(max(0.0, stake), 2))
        return BankrollAdvice(
            bankroll=round(bankroll, 2),
            profile=profile_key,
            profile_multiplier=profile_multiplier,
            stake_fraction=round(min(0.03, kelly * profile_multiplier), 4),
            suggested_stake=stake,
            max_stake_cap=max_cap,
            kelly_fraction=round(kelly, 4),
            allowed=stake > 0,
            reason="Kelly fracionado aplicado com teto de 3% da banca." if stake > 0 else "Sem stake recomendada.",
        )

