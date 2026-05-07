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
