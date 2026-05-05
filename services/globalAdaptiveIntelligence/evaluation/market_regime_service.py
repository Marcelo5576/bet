from __future__ import annotations


class MarketRegimeService:
    def classify(self, *, volatility: float, recent_over_rate: float, recent_btts_rate: float) -> dict[str, str | float]:
        if volatility >= 0.45:
            regime = "mercado volátil"
        elif recent_over_rate >= 60:
            regime = "liga em fase over"
        elif recent_btts_rate <= 35:
            regime = "liga em fase under/clean-sheet"
        else:
            regime = "mercado estável"
        return {"regime": regime, "volatility": round(volatility, 4)}

