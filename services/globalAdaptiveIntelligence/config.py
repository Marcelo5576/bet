from __future__ import annotations

from dataclasses import dataclass
import os


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class GlobalAdaptiveSettings:
    db_file: str
    default_sport: str
    default_market: str
    mock_enabled: bool
    min_confidence: float
    min_ev: float
    monte_carlo_paths: int
    monte_carlo_steps: int
    governance_auto_draft: bool


def load_global_adaptive_settings() -> GlobalAdaptiveSettings:
    return GlobalAdaptiveSettings(
        db_file=(
            os.getenv("GLOBAL_AI_DB_FILE")
            or os.getenv("FOOTBALL_RESEARCH_DB_FILE")
            or "data/global_adaptive_intelligence.db"
        ),
        default_sport=(os.getenv("GLOBAL_AI_DEFAULT_SPORT", "football").strip().lower() or "football"),
        default_market=(os.getenv("GLOBAL_AI_DEFAULT_MARKET", "match_winner_home").strip() or "match_winner_home"),
        mock_enabled=_as_bool(os.getenv("GLOBAL_AI_MOCKS_ENABLED"), True),
        min_confidence=float(os.getenv("GLOBAL_AI_MIN_CONFIDENCE", "60")),
        min_ev=float(os.getenv("GLOBAL_AI_MIN_EV", "0.03")),
        monte_carlo_paths=max(100, int(os.getenv("GLOBAL_AI_MONTE_CARLO_PATHS", "500"))),
        monte_carlo_steps=max(20, int(os.getenv("GLOBAL_AI_MONTE_CARLO_STEPS", "60"))),
        governance_auto_draft=_as_bool(os.getenv("GLOBAL_AI_GOVERNANCE_AUTO_DRAFT"), True),
    )

