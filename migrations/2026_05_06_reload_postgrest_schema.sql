notify pgrst, 'reload schema';

select table_schema, table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in (
    'historical_matches',
    'historical_stats',
    'historical_odds',
    'learning_events',
    'raw_football_imports',
    'normalized_football_data',
    'football_research_logs'
  )
order by table_name;
