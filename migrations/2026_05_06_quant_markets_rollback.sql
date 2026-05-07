-- ApexGol AI / Quant Markets rollback
-- Use somente se precisar desfazer a camada nova de mercados quantitativos.

drop table if exists public.live_market_movements;
drop table if exists public.referee_profiles;
drop table if exists public.market_pressure_snapshots;
drop table if exists public.historical_asian_lines;
drop table if exists public.historical_cards;
drop table if exists public.historical_corners;
