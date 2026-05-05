from __future__ import annotations

from dataclasses import dataclass
import os


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class ResearchSkillSettings:
    db_file: str
    csv_root: str
    mock_enabled: bool
    default_bankroll: float
    default_profile: str
    min_ev_to_recommend: float
    min_confidence_to_recommend: float
    api_football_key: str | None
    api_football_base_url: str
    football_data_org_token: str | None
    football_data_org_base_url: str
    odds_api_key: str | None
    odds_api_base_url: str
    statsbomb_open_base_url: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    auto_seed_mocks: bool


def load_research_skill_settings() -> ResearchSkillSettings:
    return ResearchSkillSettings(
        db_file=os.getenv("FOOTBALL_RESEARCH_DB_FILE", "data/football_quant_research.db"),
        csv_root=os.getenv("FOOTBALL_RESEARCH_CSV_ROOT", "data/processed"),
        mock_enabled=_as_bool(os.getenv("FOOTBALL_RESEARCH_MOCKS_ENABLED"), True),
        default_bankroll=max(100.0, float(os.getenv("FOOTBALL_RESEARCH_DEFAULT_BANKROLL", "1000"))),
        default_profile=(os.getenv("FOOTBALL_RESEARCH_DEFAULT_PROFILE", "moderado").strip().lower() or "moderado"),
        min_ev_to_recommend=float(os.getenv("FOOTBALL_RESEARCH_MIN_EV", "0.03")),
        min_confidence_to_recommend=float(os.getenv("FOOTBALL_RESEARCH_MIN_CONFIDENCE", "60")),
        api_football_key=(os.getenv("API_FOOTBALL_KEY") or "").strip() or None,
        api_football_base_url=(os.getenv("API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io").rstrip("/")),
        football_data_org_token=(os.getenv("FOOTBALL_DATA_ORG_TOKEN") or "").strip() or None,
        football_data_org_base_url=(os.getenv("FOOTBALL_DATA_ORG_BASE_URL", "https://api.football-data.org/v4").rstrip("/")),
        odds_api_key=((os.getenv("ODDS_API_KEY") or "").strip() or None),
        odds_api_base_url=(os.getenv("ODDS_API_IO_BASE_URL", "https://api.odds-api.io/v3").rstrip("/")),
        statsbomb_open_base_url=(os.getenv("STATSBOMB_OPEN_BASE_URL", "https://raw.githubusercontent.com/statsbomb/open-data/master").rstrip("/")),
        supabase_url=(os.getenv("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_service_role_key=(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip() or None,
        auto_seed_mocks=_as_bool(os.getenv("FOOTBALL_RESEARCH_AUTO_SEED_MOCKS"), True),
    )

