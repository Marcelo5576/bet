from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SourceRecord:
    name: str
    provider_type: str
    base_url: str
    api_key_env_name: str | None = None
    is_active: bool = True
    priority: int = 100


@dataclass(slots=True)
class NormalizedMatch:
    external_id: str
    league: str
    country: str
    season: int
    match_date: datetime
    home_team: str
    away_team: str
    status: str
    home_goals: int | None = None
    away_goals: int | None = None
    minute: int | None = None
    source: str = "mock"
    stats: dict[str, Any] = field(default_factory=dict)
    odds: list[dict[str, Any]] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TeamContext:
    team: str
    league: str
    sample_size: int = 0
    form_5: float = 0.0
    form_10: float = 0.0
    goals_for_avg_5: float = 0.0
    goals_against_avg_5: float = 0.0
    goals_for_avg_10: float = 0.0
    goals_against_avg_10: float = 0.0
    over_15_rate: float = 0.0
    over_25_rate: float = 0.0
    btts_rate: float = 0.0
    clean_sheet_rate: float = 0.0
    failed_to_score_rate: float = 0.0
    corner_avg: float = 0.0
    card_avg: float = 0.0
    home_away_bias: float = 0.0


@dataclass(slots=True)
class PoissonPrediction:
    home_lambda: float
    away_lambda: float
    home_win: float
    draw: float
    away_win: float
    over_25: float
    under_25: float
    btts_yes: float
    btts_no: float
    score_matrix: list[list[float]]


@dataclass(slots=True)
class ValueBetAssessment:
    offered_odd: float | None
    estimated_probability: float
    implied_probability: float | None
    fair_odd: float | None
    expected_value: float | None
    band: str
    allowed: bool
    reason: str


@dataclass(slots=True)
class BankrollAdvice:
    bankroll: float
    profile: str
    profile_multiplier: float
    stake_fraction: float
    suggested_stake: float
    max_stake_cap: float
    kelly_fraction: float
    allowed: bool
    reason: str


@dataclass(slots=True)
class MatchPrediction:
    match_id: int
    market: str
    recommendation: str
    confidence_score: float
    risk_level: str
    estimated_probability: float
    fair_odd: float | None
    offered_odd: float | None
    expected_value: float | None
    value_band: str
    explanation: dict[str, Any]
    bankroll: BankrollAdvice
    model_version: str
    created_at: datetime


@dataclass(slots=True)
class BacktestRequest:
    league: str | None = None
    season: int | None = None
    market: str = "match_winner_home"
    ev_min: float = 0.0
    confidence_min: float = 55.0
    date_from: str | None = None
    date_to: str | None = None
    bankroll: float = 1000.0
    bankroll_profile: str = "moderado"
    model_version: str = "baseline"
    user_id: int | None = None


@dataclass(slots=True)
class BacktestSummary:
    simulation_run_id: int
    total_games: int
    total_entries: int
    hit_rate: float
    roi: float
    profit_loss: float
    initial_bankroll: float
    final_bankroll: float
    drawdown_max: float
    by_league: list[dict[str, Any]]
    by_market: list[dict[str, Any]]
    by_odds_range: list[dict[str, Any]]
    by_ev_band: list[dict[str, Any]]

