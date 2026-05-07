from dataclasses import dataclass
import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - fallback para runtimes enxutos
    def load_dotenv(*args, **kwargs):
        return False


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_confidence(value: str | None, default: int = 62) -> int:
    if value is None or not value.strip():
        return default
    number = float(value)
    if 0 < number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


@dataclass(frozen=True)
class Settings:
    product_name: str
    product_tagline: str
    website_url: str
    sales_whatsapp: str
    sales_email: str
    plan_starter_price_brl: float
    plan_pro_price_brl: float
    plan_team_price_brl: float
    telegram_bot_token: str
    scan_interval_seconds: int
    idle_scan_interval_seconds: int
    active_scan_interval_seconds: int
    pregame_scan_interval_seconds: int
    pregame_lead_minutes: int
    pregame_shortlist_limit: int
    test_mode: bool
    api_football_key: str | None
    api_football_base_url: str
    football_data_org_token: str | None
    odds_api_io_key: str | None
    odds_api_io_base_url: str
    odds_api_io_bookmakers: str
    gemini_max_rpm: int
    odds_max_rpm: int
    events_live_ttl: int
    odds_event_ttl: int
    refine_signal_ttl: int
    provider_cooldown_429: int
    min_score_to_refine: int
    min_confidence_to_refine: int
    gemini_api_key: str | None
    gemini_model: str
    state_file: str
    brain_enabled: bool
    brain_db_file: str
    decision_audit_db_file: str
    min_confidence: int
    bankroll: float
    unit_percent: float
    max_stake_units: float
    min_history_for_enter: int
    min_edge_to_enter: float
    kelly_fraction: float
    daily_red_limit: int
    block_esports: bool
    dashboard_user: str
    dashboard_password: str
    dashboard_domains: str
    support_note: str
    portal_db_file: str
    usage_metrics_db_file: str
    portal_session_secret: str
    portal_session_hours: int
    portal_trial_days: int
    admin_email: str
    admin_name: str
    admin_password: str
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_from: str | None
    smtp_starttls: bool
    payment_gateway: str
    payment_currency: str
    stripe_secret_key: str | None
    stripe_publishable_key: str | None
    stripe_price_starter: str | None
    stripe_price_pro: str | None
    stripe_price_team: str | None
    mercadopago_access_token: str | None
    supabase_url: str | None
    supabase_service_role_key: str | None
    gemini_input_cost_per_1m_brl: float
    gemini_output_cost_per_1m_brl: float
    api_football_cost_per_request_brl: float
    football_data_org_cost_per_request_brl: float
    odds_api_io_cost_per_request_brl: float
    espn_cost_per_request_brl: float
    supabase_cost_per_request_brl: float
    stripe_cost_per_request_brl: float
    mercadopago_cost_per_request_brl: float
    auto_simulation_enabled: bool
    auto_simulation_hour: int
    auto_simulation_timezone: str
    auto_simulation_games: int
    auto_simulation_bankroll: float
    auto_simulation_stake_percent: float


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        product_name=os.getenv("PRODUCT_NAME", "ApexGol AI").strip() or "ApexGol AI",
        product_tagline=os.getenv(
            "PRODUCT_TAGLINE",
            "Central Quantitativa de Inteligência Esportiva.",
        ).strip(),
        website_url=os.getenv("WEBSITE_URL", "https://novo.tickpost.com.br").strip(),
        sales_whatsapp=os.getenv("SALES_WHATSAPP", "").strip(),
        sales_email=os.getenv("SALES_EMAIL", "").strip(),
        plan_starter_price_brl=float(os.getenv("PLAN_STARTER_PRICE_BRL", "97")),
        plan_pro_price_brl=float(os.getenv("PLAN_PRO_PRICE_BRL", "197")),
        plan_team_price_brl=float(os.getenv("PLAN_TEAM_PRICE_BRL", "497")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        scan_interval_seconds=int(os.getenv("SCAN_INTERVAL_SECONDS", "20")),
        idle_scan_interval_seconds=int(os.getenv("IDLE_SCAN_INTERVAL_SECONDS", "20")),
        active_scan_interval_seconds=int(os.getenv("ACTIVE_SCAN_INTERVAL_SECONDS", "20")),
        pregame_scan_interval_seconds=int(os.getenv("PREGAME_SCAN_INTERVAL_SECONDS", "300")),
        pregame_lead_minutes=max(15, min(360, int(os.getenv("PREGAME_LEAD_MINUTES", "150")))),
        pregame_shortlist_limit=max(4, min(24, int(os.getenv("PREGAME_SHORTLIST_LIMIT", "12")))),
        test_mode=_as_bool(os.getenv("TEST_MODE"), False),
        api_football_key=os.getenv("API_FOOTBALL_KEY") or None,
        api_football_base_url=os.getenv(
            "API_FOOTBALL_BASE_URL", "https://v3.football.api-sports.io"
        ).rstrip("/"),
        football_data_org_token=(os.getenv("FOOTBALL_DATA_ORG_TOKEN") or "").strip() or None,
        odds_api_io_key=(
            (os.getenv("ODDS_API_KEY") or "").strip()
            or (os.getenv("ODDS_API_IO_KEY") or "").strip()
            or None
        ),
        odds_api_io_base_url=os.getenv(
            "ODDS_API_IO_BASE_URL", "https://api.odds-api.io/v3"
        ).rstrip("/"),
        odds_api_io_bookmakers=(
            os.getenv("ODDS_API_IO_BOOKMAKERS", "Bet365").strip() or "Bet365"
        ),
        gemini_max_rpm=max(1, int(os.getenv("GEMINI_MAX_RPM", "10"))),
        odds_max_rpm=max(1, int(os.getenv("ODDS_MAX_RPM", "20"))),
        events_live_ttl=max(30, int(os.getenv("EVENTS_LIVE_TTL", "30"))),
        odds_event_ttl=max(30, int(os.getenv("ODDS_EVENT_TTL", "45"))),
        refine_signal_ttl=max(900, int(os.getenv("REFINE_SIGNAL_TTL", "900"))),
        provider_cooldown_429=max(15, int(os.getenv("PROVIDER_COOLDOWN_429", "60"))),
        min_score_to_refine=max(0, min(100, int(os.getenv("MIN_SCORE_TO_REFINE", "60")))),
        min_confidence_to_refine=max(
            0,
            min(100, int(os.getenv("MIN_CONFIDENCE_TO_REFINE", "55"))),
        ),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        state_file=os.getenv("STATE_FILE", "data/state.json"),
        brain_enabled=_as_bool(os.getenv("BRAIN_ENABLED"), True),
        brain_db_file=os.getenv("BRAIN_DB_FILE", "data/football_brain.db"),
        decision_audit_db_file=os.getenv("DECISION_AUDIT_DB_FILE", "data/decision_audit.db"),
        min_confidence=_as_confidence(os.getenv("MIN_CONFIDENCE"), 62),
        bankroll=float(os.getenv("BANKROLL", "1000")),
        unit_percent=float(os.getenv("UNIT_PERCENT", "1")),
        max_stake_units=float(os.getenv("MAX_STAKE_UNITS", "2")),
        min_history_for_enter=int(os.getenv("MIN_HISTORY_FOR_ENTER", "30")),
        min_edge_to_enter=float(os.getenv("MIN_EDGE_TO_ENTER", "0.05")),
        kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
        daily_red_limit=int(os.getenv("DAILY_RED_LIMIT", "2")),
        block_esports=_as_bool(os.getenv("BLOCK_ESPORTS"), True),
        dashboard_user=os.getenv("DASHBOARD_USER", "admin"),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "change-me-now"),
        dashboard_domains=os.getenv(
            "DASHBOARD_DOMAINS",
            "http://2.24.217.214,http://novo.tickpost.com.br",
        ),
        support_note=os.getenv(
            "SUPPORT_NOTE",
            "Se der problema, envie /suporte no Telegram e compartilhe o diagnostico comigo no Codex.",
        ),
        portal_db_file=os.getenv("PORTAL_DB_FILE", "data/portal.db"),
        usage_metrics_db_file=os.getenv("USAGE_METRICS_DB_FILE", "data/usage_metrics.db"),
        portal_session_secret=(
            os.getenv("PORTAL_SESSION_SECRET")
            or os.getenv("DASHBOARD_PASSWORD")
            or "change-this-session-secret"
        ).strip(),
        portal_session_hours=int(os.getenv("PORTAL_SESSION_HOURS", "24")),
        portal_trial_days=int(os.getenv("PORTAL_TRIAL_DAYS", "7")),
        admin_email=os.getenv("ADMIN_EMAIL", "admin@betsignal.local").strip().lower(),
        admin_name=os.getenv("ADMIN_NAME", "Administrador BetSignal").strip() or "Administrador BetSignal",
        admin_password=(
            os.getenv("ADMIN_PASSWORD")
            or os.getenv("DASHBOARD_PASSWORD")
            or "change-me-now"
        ),
        smtp_host=(os.getenv("SMTP_HOST") or "").strip() or None,
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=(os.getenv("SMTP_USER") or "").strip() or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_from=(os.getenv("SMTP_FROM") or "").strip() or None,
        smtp_starttls=_as_bool(os.getenv("SMTP_STARTTLS"), True),
        payment_gateway=os.getenv("PAYMENT_GATEWAY", "stripe").strip().lower(),
        payment_currency=os.getenv("PAYMENT_CURRENCY", "BRL").strip().upper(),
        stripe_secret_key=(os.getenv("STRIPE_SECRET_KEY") or "").strip() or None,
        stripe_publishable_key=(os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip() or None,
        stripe_price_starter=(os.getenv("STRIPE_PRICE_STARTER") or "").strip() or None,
        stripe_price_pro=(os.getenv("STRIPE_PRICE_PRO") or "").strip() or None,
        stripe_price_team=(os.getenv("STRIPE_PRICE_TEAM") or "").strip() or None,
        mercadopago_access_token=(os.getenv("MERCADOPAGO_ACCESS_TOKEN") or "").strip() or None,
        supabase_url=(os.getenv("SUPABASE_URL") or "").rstrip("/") or None,
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or None,
        gemini_input_cost_per_1m_brl=float(os.getenv("GEMINI_INPUT_COST_PER_1M_BRL", "0") or 0),
        gemini_output_cost_per_1m_brl=float(os.getenv("GEMINI_OUTPUT_COST_PER_1M_BRL", "0") or 0),
        api_football_cost_per_request_brl=float(os.getenv("API_FOOTBALL_COST_PER_REQUEST_BRL", "0") or 0),
        football_data_org_cost_per_request_brl=float(
            os.getenv("FOOTBALL_DATA_ORG_COST_PER_REQUEST_BRL", "0") or 0
        ),
        odds_api_io_cost_per_request_brl=float(
            os.getenv("ODDS_API_IO_COST_PER_REQUEST_BRL", "0") or 0
        ),
        espn_cost_per_request_brl=float(os.getenv("ESPN_COST_PER_REQUEST_BRL", "0") or 0),
        supabase_cost_per_request_brl=float(os.getenv("SUPABASE_COST_PER_REQUEST_BRL", "0") or 0),
        stripe_cost_per_request_brl=float(os.getenv("STRIPE_COST_PER_REQUEST_BRL", "0") or 0),
        mercadopago_cost_per_request_brl=float(
            os.getenv("MERCADOPAGO_COST_PER_REQUEST_BRL", "0") or 0
        ),
        auto_simulation_enabled=_as_bool(os.getenv("AUTO_SIMULATION_ENABLED"), True),
        auto_simulation_hour=max(0, min(23, int(os.getenv("AUTO_SIMULATION_HOUR", "6")))),
        auto_simulation_timezone=(
            os.getenv("AUTO_SIMULATION_TIMEZONE", "America/Sao_Paulo").strip()
            or "America/Sao_Paulo"
        ),
        auto_simulation_games=max(30, min(120, int(os.getenv("AUTO_SIMULATION_GAMES", "30")))),
        auto_simulation_bankroll=max(
            20.0, min(10000.0, float(os.getenv("AUTO_SIMULATION_BANKROLL", "100")))
        ),
        auto_simulation_stake_percent=max(
            1.0, min(20.0, float(os.getenv("AUTO_SIMULATION_STAKE_PERCENT", "10")))
        ),
    )
