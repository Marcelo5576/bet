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
