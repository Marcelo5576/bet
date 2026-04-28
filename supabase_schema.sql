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
