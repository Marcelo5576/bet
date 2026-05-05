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

