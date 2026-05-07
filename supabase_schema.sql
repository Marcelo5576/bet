-- ApexGol AI full Supabase schema
-- Este arquivo consolida o schema principal e as migrations públicas necessárias.
-- Pode ser aplicado no Supabase SQL Editor em um projeto novo.

create extension if not exists pgcrypto;

create table if not exists public.betsignal_games (
  game_id text primary key,
  league text,
  division text,
  home text,
  away text,
  minute integer,
  home_goals integer,
  away_goals integer,
  home_pressure integer,
  away_pressure integer,
  home_shots_on integer,
  away_shots_on integer,
  odds_home numeric,
  odds_draw numeric,
  odds_away numeric,
  priority integer,
  markets jsonb not null default '{}'::jsonb,
  raw jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.betsignal_signals (
  signal_id text primary key,
  game_id text references public.betsignal_games(game_id) on delete set null,
  action text,
  team text,
  market text,
  confidence integer,
  target_odds numeric,
  estimated_probability numeric,
  implied_probability numeric,
  value_edge numeric,
  fair_odds numeric,
  data_quality integer,
  stake_units numeric,
  stake_value numeric,
  entered boolean not null default false,
  entered_at timestamptz,
  entry_market text,
  entry_value numeric,
  entry_odds numeric,
  entry_notes text,
  outcome text not null default 'open',
  profit_units numeric,
  created_at timestamptz,
  finished_at timestamptz,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists betsignal_games_updated_at_idx on public.betsignal_games(updated_at desc);
create index if not exists betsignal_signals_created_at_idx on public.betsignal_signals(created_at desc);
create index if not exists betsignal_signals_outcome_idx on public.betsignal_signals(outcome);

create table if not exists public.betsignal_ai_memory (
  memory_id text primary key,
  scope text not null,
  subject text not null,
  source text not null,
  sample_size integer not null default 0,
  hit_rate numeric,
  roi_units numeric,
  profit_units numeric,
  avg_confidence numeric,
  avg_edge numeric,
  notes text,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists betsignal_ai_memory_scope_subject_idx
  on public.betsignal_ai_memory(scope, subject);

create index if not exists betsignal_ai_memory_updated_at_idx
  on public.betsignal_ai_memory(updated_at desc);

create table if not exists public.betsignal_ai_skills (
  skill_id text primary key,
  title text not null,
  intent text not null,
  keywords text[] not null default '{}'::text[],
  answer text not null,
  priority integer not null default 100,
  active boolean not null default true,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists betsignal_ai_skills_active_priority_idx
  on public.betsignal_ai_skills(active, priority);

create table if not exists public.betsignal_simulations (
  simulation_id text primary key,
  trigger text not null,
  scan_scope text,
  source_games integer,
  total_games integer not null default 0,
  greens integer not null default 0,
  reds integer not null default 0,
  hit_rate numeric,
  profit_units numeric,
  roi numeric,
  max_drawdown numeric,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists betsignal_simulations_created_at_idx
  on public.betsignal_simulations(created_at desc);

create index if not exists betsignal_simulations_trigger_idx
  on public.betsignal_simulations(trigger);


-- Migration: global adaptive intelligence
-- Global Adaptive Sports & Market Intelligence Platform
-- Reversível por companion rollback file.
-- Mantém tabelas atuais intactas e cria camadas novas de pesquisa, ensemble,
-- agentes, Monte Carlo, governança e memória longa.

create table if not exists public.data_sources (
  id bigserial primary key,
  user_id uuid null,
  name text not null unique,
  domain text,
  provider_type text not null,
  sport_or_market text not null default 'football',
  base_url text,
  api_key_env_name text,
  is_active boolean not null default true,
  priority integer not null default 100,
  rate_limit_per_minute integer not null default 0,
  requires_api_key boolean not null default false,
  status text not null default 'ready',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.raw_imports (
  id bigserial primary key,
  user_id uuid null,
  source_name text not null,
  sport_or_market text not null,
  external_ref text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.normalized_entities (
  id bigserial primary key,
  user_id uuid null,
  entity_type text not null,
  entity_key text not null,
  entity_name text not null,
  sport_or_market text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.normalized_events (
  id bigserial primary key,
  user_id uuid null,
  external_event_id text not null,
  sport_or_market text not null,
  league text,
  season text,
  event_date timestamptz not null,
  home_label text,
  away_label text,
  status text,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.normalized_odds (
  id bigserial primary key,
  user_id uuid null,
  event_id text,
  sport_or_market text not null,
  market text not null,
  line text,
  source_name text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.normalized_stats (
  id bigserial primary key,
  user_id uuid null,
  event_id text,
  sport_or_market text not null,
  source_name text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.ensemble_configs (
  id bigserial primary key,
  user_id uuid null,
  name text not null,
  version text not null,
  context_type text not null,
  sport_or_market text not null,
  market text not null,
  weights jsonb not null default '{}'::jsonb,
  is_active boolean not null default false,
  status text not null default 'draft',
  created_at timestamptz not null default now()
);

create table if not exists public.backtest_batches (
  id bigserial primary key,
  user_id uuid null,
  label text not null,
  sport_or_market text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.monte_carlo_runs (
  id bigserial primary key,
  user_id uuid null,
  label text not null,
  sport_or_market text not null,
  market text not null,
  paths integer not null,
  steps integer not null,
  initial_bankroll numeric not null,
  ruin_risk numeric not null,
  median_final_bankroll numeric not null,
  p10_final_bankroll numeric not null,
  p90_final_bankroll numeric not null,
  created_at timestamptz not null default now()
);

create table if not exists public.monte_carlo_results (
  id bigserial primary key,
  user_id uuid null,
  run_id bigint references public.monte_carlo_runs(id) on delete cascade,
  path_index integer not null,
  final_bankroll numeric not null,
  max_drawdown numeric not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.strategy_versions (
  id bigserial primary key,
  user_id uuid null,
  name text not null,
  version text not null,
  sport_or_market text not null,
  market text not null,
  rules jsonb not null default '{}'::jsonb,
  status text not null default 'draft',
  created_at timestamptz not null default now()
);

create table if not exists public.strategy_experiments (
  id bigserial primary key,
  user_id uuid null,
  strategy_version_id bigint references public.strategy_versions(id) on delete set null,
  label text not null,
  payload jsonb not null default '{}'::jsonb,
  fitness_score numeric not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.strategy_population (
  id bigserial primary key,
  user_id uuid null,
  generation integer not null,
  strategy_version_id bigint references public.strategy_versions(id) on delete set null,
  genome jsonb not null default '{}'::jsonb,
  fitness_score numeric not null,
  created_at timestamptz not null default now()
);

create table if not exists public.agents (
  id bigserial primary key,
  name text not null unique,
  role text not null,
  sport_or_market text not null,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.agent_outputs (
  id bigserial primary key,
  user_id uuid null,
  event_id text,
  market text,
  agent_name text not null,
  decision text not null,
  trust_score numeric not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.agent_trust_scores (
  id bigserial primary key,
  user_id uuid null,
  agent_name text not null,
  context_type text not null,
  trust_score numeric not null,
  sample_size integer not null default 0,
  updated_at timestamptz not null default now(),
  unique(agent_name, context_type)
);

create table if not exists public.consensus_decisions (
  id bigserial primary key,
  user_id uuid null,
  event_id text,
  market text,
  selection text,
  agent_outputs jsonb not null default '[]'::jsonb,
  final_decision text not null,
  trust_score numeric not null,
  reasons jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.meta_model_decisions (
  id bigserial primary key,
  user_id uuid null,
  event_id text,
  market text,
  selected_model text not null,
  trust_score numeric not null,
  reason text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.generated_features (
  id bigserial primary key,
  user_id uuid null,
  sport_or_market text not null,
  feature_name text not null,
  scope text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.feature_performance (
  id bigserial primary key,
  user_id uuid null,
  feature_name text not null,
  impact_score numeric not null,
  stability_score numeric not null,
  sample_size integer not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.drift_events (
  id bigserial primary key,
  user_id uuid null,
  drift_type text not null,
  scope text not null,
  severity text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.pattern_insights (
  id bigserial primary key,
  user_id uuid null,
  insight_type text not null,
  label text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.long_term_memory (
  id bigserial primary key,
  user_id uuid null,
  memory_type text not null,
  title text not null,
  body text not null,
  search_text text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.risk_events (
  id bigserial primary key,
  user_id uuid null,
  risk_type text not null,
  severity text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.exposure_snapshots (
  id bigserial primary key,
  user_id uuid null,
  sport_or_market text not null,
  total_exposure numeric not null,
  risk_of_ruin numeric not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.approval_requests (
  id bigserial primary key,
  user_id uuid null,
  change_type text not null,
  target_ref text not null,
  status text not null default 'pending',
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

create table if not exists public.change_history (
  id bigserial primary key,
  user_id uuid null,
  change_type text not null,
  target_ref text not null,
  action text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.rollback_points (
  id bigserial primary key,
  user_id uuid null,
  label text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.analysis_logs (
  id bigserial primary key,
  user_id uuid null,
  component text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.system_logs (
  id bigserial primary key,
  user_id uuid null,
  component text not null,
  level text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_data_sources_status_created on public.data_sources(status, created_at);
create index if not exists idx_normalized_events_league_season on public.normalized_events(league, season);
create index if not exists idx_normalized_events_event_date on public.normalized_events(event_date);
create index if not exists idx_normalized_odds_market_created on public.normalized_odds(market, created_at);
create index if not exists idx_agent_outputs_event_market on public.agent_outputs(event_id, market, created_at);
create index if not exists idx_consensus_decisions_event_market on public.consensus_decisions(event_id, market, created_at);
create index if not exists idx_meta_model_created on public.meta_model_decisions(created_at);
create index if not exists idx_mc_results_run on public.monte_carlo_results(run_id);
create index if not exists idx_strategy_population_generation on public.strategy_population(generation, created_at);
create index if not exists idx_generated_features_name on public.generated_features(feature_name, created_at);
create index if not exists idx_drift_events_type_created on public.drift_events(drift_type, created_at);
create index if not exists idx_long_term_memory_search_created on public.long_term_memory(memory_type, created_at);
create index if not exists idx_approval_requests_status_created on public.approval_requests(status, created_at);

comment on table public.data_sources is 'Registro plugável de fontes de dados do núcleo global.';
comment on table public.approval_requests is 'Mudanças sensíveis precisam passar por aprovação humana antes de ativação.';



-- Migration: football research history
begin;

create table if not exists public.historical_matches (
  id bigserial primary key,
  user_id uuid null,
  external_id text not null,
  league text not null,
  country text,
  season integer,
  match_date timestamptz not null,
  home_team text not null,
  away_team text not null,
  status text not null,
  home_goals integer,
  away_goals integer,
  source text not null,
  raw_json text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (external_id, source)
);

create table if not exists public.historical_odds (
  id bigserial primary key,
  user_id uuid null,
  historical_match_id bigint not null,
  timestamp timestamptz not null,
  market text not null,
  line text,
  home_odd numeric,
  draw_odd numeric,
  away_odd numeric,
  over_odd numeric,
  under_odd numeric,
  bookmaker text,
  source text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.historical_stats (
  id bigserial primary key,
  user_id uuid null,
  historical_match_id bigint not null,
  possession_home numeric,
  possession_away numeric,
  shots_home integer,
  shots_away integer,
  shots_on_home integer,
  shots_on_away integer,
  corners_home integer,
  corners_away integer,
  yellow_home integer,
  yellow_away integer,
  red_home integer,
  red_away integer,
  dangerous_attacks_home integer,
  dangerous_attacks_away integer,
  attacks_home integer,
  attacks_away integer,
  xg_home numeric,
  xg_away numeric,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (historical_match_id)
);

create table if not exists public.learning_events (
  id bigserial primary key,
  user_id uuid null,
  event_type text not null,
  ref_type text,
  ref_id text,
  payload_json text not null,
  created_at timestamptz not null default now()
);

create unique index if not exists idx_learning_events_ref_unique
  on public.learning_events(event_type, ref_type, ref_id)
  where ref_id is not null;

create table if not exists public.raw_football_imports (
  id bigserial primary key,
  user_id uuid null,
  source_name text not null,
  external_ref text,
  payload_json text not null,
  imported_at timestamptz not null default now()
);

create unique index if not exists idx_raw_football_imports_source_ref
  on public.raw_football_imports(source_name, external_ref)
  where external_ref is not null;

create table if not exists public.normalized_football_data (
  id bigserial primary key,
  user_id uuid null,
  entity_type text not null,
  entity_key text not null,
  normalized_json text not null,
  source_name text not null,
  created_at timestamptz not null default now()
);

create unique index if not exists idx_normalized_football_data_source_key
  on public.normalized_football_data(source_name, entity_key);

create table if not exists public.football_research_logs (
  id bigserial primary key,
  user_id uuid null,
  level text not null,
  component text not null,
  message text not null,
  payload_json text not null,
  created_at timestamptz not null default now()
);

create index if not exists idx_historical_matches_league_season_date
  on public.historical_matches(league, season, match_date);

create index if not exists idx_historical_matches_source
  on public.historical_matches(source, external_id);

create index if not exists idx_historical_odds_market
  on public.historical_odds(market, timestamp);

create index if not exists idx_learning_events_type_created
  on public.learning_events(event_type, created_at);

create index if not exists idx_research_logs_component_created
  on public.football_research_logs(component, created_at);

commit;


-- Migration: historical quality features
-- ApexGol AI / Football Quant AI Research Skill
-- Historical quality, feature store, temporal split and league reliability.
-- Safe to run more than once in Supabase SQL Editor.

alter table if exists public.historical_matches
  add column if not exists external_fixture_id text,
  add column if not exists source_provider text,
  add column if not exists league_id text,
  add column if not exists league_name text,
  add column if not exists normalized_payload text,
  add column if not exists data_quality_score integer not null default 0,
  add column if not exists usable_for_training boolean not null default false,
  add column if not exists duplicate_key text,
  add column if not exists temporal_split text,
  add column if not exists import_batch_id text,
  add column if not exists imported_at timestamptz;

alter table if exists public.historical_odds
  add column if not exists odds_phase text not null default 'pregame',
  add column if not exists is_real boolean not null default true,
  add column if not exists raw_json text,
  add column if not exists imported_at timestamptz;

alter table if exists public.historical_stats
  add column if not exists raw_json text;

create table if not exists public.historical_features (
  id bigserial primary key,
  user_id bigint,
  match_id bigint not null,
  feature_set_version text not null,
  temporal_split text,
  home_recent_form_5 numeric,
  away_recent_form_5 numeric,
  home_goals_avg_5 numeric,
  away_goals_avg_5 numeric,
  home_conceded_avg_5 numeric,
  away_conceded_avg_5 numeric,
  home_xg_avg_5 numeric,
  away_xg_avg_5 numeric,
  home_strength numeric,
  away_strength numeric,
  market_implied_probability numeric,
  closing_line_value numeric,
  data_quality_score integer not null default 0,
  usable_for_training boolean not null default false,
  context_match_count integer not null default 0,
  created_at timestamptz not null default now(),
  unique(match_id, feature_set_version)
);

create table if not exists public.league_reliability_scores (
  id bigserial primary key,
  user_id bigint,
  league text not null,
  season integer,
  match_count integer not null default 0,
  trainable_count integer not null default 0,
  odds_count integer not null default 0,
  stats_count integer not null default 0,
  avg_data_quality numeric not null default 0,
  roi_simulated numeric not null default 0,
  drawdown numeric not null default 0,
  stability_score numeric not null default 0,
  league_reliability_score numeric not null default 0,
  classification text not null,
  reasons_json text not null default '[]',
  calculated_at timestamptz not null default now(),
  unique(league, season)
);

create table if not exists public.historical_import_batches (
  id bigserial primary key,
  batch_key text not null unique,
  source_provider text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  imported_matches integer not null default 0,
  duplicates_blocked integer not null default 0,
  errors_count integer not null default 0,
  payload_json text not null default '{}'
);

create index if not exists idx_hist_matches_source_fixture
  on public.historical_matches(source_provider, external_fixture_id);
create index if not exists idx_hist_matches_quality
  on public.historical_matches(data_quality_score, usable_for_training);
create index if not exists idx_hist_matches_split
  on public.historical_matches(temporal_split, match_date);
create index if not exists idx_hist_features_match_version
  on public.historical_features(match_id, feature_set_version);
create index if not exists idx_hist_features_split
  on public.historical_features(temporal_split, usable_for_training);
create index if not exists idx_league_reliability_score
  on public.league_reliability_scores(league_reliability_score, classification);

comment on table public.historical_features is
  'Feature store temporal sem vazamento: cada linha deve usar apenas dados anteriores ao jogo.';
comment on column public.historical_matches.data_quality_score is
  '+25 placar confirmado, +25 odds reais, +20 stats, +15 normalização liga/time, +15 sem conflito.';


-- Migration: quant markets
-- ApexGol AI / Quant Markets
-- Escanteios, cartões, asiáticas, pressão live e movimentos de mercado.
-- Seguro para rodar mais de uma vez no Supabase SQL Editor.

create table if not exists public.historical_corners (
  id bigserial primary key,
  user_id bigint,
  historical_match_id bigint,
  external_fixture_id text,
  source_provider text not null,
  period text not null default 'FT',
  corners_home integer,
  corners_away integer,
  corners_total integer,
  line text,
  over_odd numeric,
  under_odd numeric,
  bookmaker text,
  is_real boolean not null default true,
  raw_json text,
  imported_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table if not exists public.historical_cards (
  id bigserial primary key,
  user_id bigint,
  historical_match_id bigint,
  external_fixture_id text,
  source_provider text not null,
  period text not null default 'FT',
  yellow_home integer,
  yellow_away integer,
  red_home integer,
  red_away integer,
  cards_total numeric,
  line text,
  over_odd numeric,
  under_odd numeric,
  bookmaker text,
  referee_name text,
  is_real boolean not null default true,
  raw_json text,
  imported_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table if not exists public.historical_asian_lines (
  id bigserial primary key,
  user_id bigint,
  historical_match_id bigint,
  external_fixture_id text,
  source_provider text not null,
  market_type text not null,
  period text not null default 'FT',
  line text,
  home_odd numeric,
  away_odd numeric,
  over_odd numeric,
  under_odd numeric,
  bookmaker text,
  is_real boolean not null default true,
  raw_json text,
  imported_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create table if not exists public.market_pressure_snapshots (
  id bigserial primary key,
  user_id bigint,
  match_id text not null,
  source_provider text,
  captured_at timestamptz not null default now(),
  minute integer,
  pressure_home numeric,
  pressure_away numeric,
  momentum_score numeric,
  territorial_dominance text,
  shots_on_home integer,
  shots_on_away integer,
  dangerous_attacks_home integer,
  dangerous_attacks_away integer,
  corners_home integer,
  corners_away integer,
  raw_json text,
  created_at timestamptz not null default now()
);

create table if not exists public.referee_profiles (
  id bigserial primary key,
  user_id bigint,
  referee_name text not null,
  league text,
  country text,
  matches_count integer not null default 0,
  cards_avg numeric,
  yellow_avg numeric,
  red_avg numeric,
  fouls_avg numeric,
  cards_ht_avg numeric,
  cards_st_avg numeric,
  aggression_index numeric,
  source_provider text,
  raw_json text,
  updated_at timestamptz not null default now(),
  unique(referee_name, league, country)
);

create table if not exists public.live_market_movements (
  id bigserial primary key,
  user_id bigint,
  match_id text not null,
  source_provider text not null,
  market_type text not null,
  selection text,
  line text,
  period text not null default 'FT',
  odd numeric,
  previous_odd numeric,
  movement numeric,
  steam_detected boolean not null default false,
  liquidity_status text,
  raw_json text,
  captured_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists idx_historical_corners_match
  on public.historical_corners(historical_match_id, period);
create index if not exists idx_historical_cards_match
  on public.historical_cards(historical_match_id, period);
create index if not exists idx_historical_asian_match
  on public.historical_asian_lines(historical_match_id, market_type, period);
create index if not exists idx_market_pressure_match_time
  on public.market_pressure_snapshots(match_id, captured_at desc);
create index if not exists idx_referee_profiles_league
  on public.referee_profiles(league, referee_name);
create index if not exists idx_live_market_movements_match
  on public.live_market_movements(match_id, market_type, captured_at desc);

comment on table public.historical_corners is 'Mercados e estatísticas históricas reais de escanteios. Não misturar odds mockadas.';
comment on table public.historical_cards is 'Mercados e estatísticas históricas reais de cartões, incluindo árbitro quando disponível.';
comment on table public.historical_asian_lines is 'Linhas asiáticas reais por fixture/provedor para backtesting e CLV.';
comment on table public.market_pressure_snapshots is 'Snapshots reais de pressão/momentum ao vivo usados pela inteligência quantitativa.';
comment on table public.live_market_movements is 'Movimentos de odds live, steam e drift por mercado confirmado.';


-- Opcional: depois de aplicar tudo, rode migrations/2026_05_06_reload_postgrest_schema.sql se precisar recarregar o PostgREST.