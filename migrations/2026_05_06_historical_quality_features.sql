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
