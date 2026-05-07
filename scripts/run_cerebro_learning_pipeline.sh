#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

LOG_DIR="${CEREBRO_LOG_DIR:-data/logs}"
mkdir -p "$LOG_DIR" data/raw/api_football_historical data/raw/api_football_odds

YEARS="${CEREBRO_YEARS:-3}"
MAX_LEAGUES="${CEREBRO_MAX_LEAGUES:-18}"
INGEST_REQUESTS="${CEREBRO_INGEST_REQUESTS:-180}"
ODDS_REQUESTS="${CEREBRO_ODDS_REQUESTS:-420}"
ODDS_LIMIT="${CEREBRO_ODDS_LIMIT:-900}"
RATE_LIMIT_SECONDS="${CEREBRO_RATE_LIMIT_SECONDS:-1.2}"
CALIBRATION_MATCHES="${CEREBRO_CALIBRATION_MATCHES:-3000}"
EVALUATION_LIMIT="${CEREBRO_EVALUATION_LIMIT:-1200}"
BACKTEST_MAX_LEAGUES="${CEREBRO_BACKTEST_MAX_LEAGUES:-8}"
BACKTEST_MIN_TRAINABLE="${CEREBRO_BACKTEST_MIN_TRAINABLE:-10}"
BACKTEST_MARKETS="${CEREBRO_BACKTEST_MARKETS:-match_winner_home,over_2_5,btts_yes}"
WITH_STATS="${CEREBRO_WITH_STATS:-0}"
WITH_ODDS_DURING_INGEST="${CEREBRO_WITH_ODDS_DURING_INGEST:-0}"

echo "==> Cérebro IA learning pipeline"
date -u +"started_at=%Y-%m-%dT%H:%M:%SZ"
echo "years=$YEARS max_leagues=$MAX_LEAGUES ingest_requests=$INGEST_REQUESTS odds_requests=$ODDS_REQUESTS"

python - <<'PY'
from src.config import load_settings
from services.footballQuantAiSkill.config import load_research_skill_settings
s = load_settings()
r = load_research_skill_settings()
print("api_football_configured=", bool(s.api_football_key or r.api_football_key))
print("supabase_configured=", bool((s.supabase_url or r.supabase_url) and (s.supabase_service_role_key or r.supabase_service_role_key)))
print("research_db=", r.db_file)
PY

INGEST_FLAGS=""
if [ "$WITH_STATS" = "1" ]; then
  INGEST_FLAGS="$INGEST_FLAGS --with-stats"
fi
if [ "$WITH_ODDS_DURING_INGEST" = "1" ]; then
  INGEST_FLAGS="$INGEST_FLAGS --with-odds"
fi

echo "==> 1/8 Ingestão histórica API-Football"
# shellcheck disable=SC2086
python scripts/ingest_api_football_history.py \
  --years "$YEARS" \
  --max-leagues "$MAX_LEAGUES" \
  --max-requests "$INGEST_REQUESTS" \
  --rate-limit-seconds "$RATE_LIMIT_SECONDS" \
  $INGEST_FLAGS

echo "==> 2/8 Backfill de odds reais"
python scripts/backfill_api_football_odds.py \
  --limit "$ODDS_LIMIT" \
  --max-requests "$ODDS_REQUESTS" \
  --rate-limit-seconds "$RATE_LIMIT_SECONDS"

echo "==> 3/8 Recalcular qualidade, split temporal e features"
python scripts/rebuild_historical_quality_features.py

echo "==> 4/8 Calibrar aprendizado histórico sem odds"
python scripts/calibrate_historical_learning.py \
  --max-matches "$CALIBRATION_MATCHES" \
  --replace

echo "==> 5/8 Avaliar EV com odds históricas reais"
python scripts/evaluate_historical_odds_learning.py \
  --limit "$EVALUATION_LIMIT" \
  --ev-min 0.05 \
  --confidence-min 65 \
  --replace

echo "==> 6/8 Rodar backtests salvos"
python scripts/run_historical_backtests.py \
  --markets "$BACKTEST_MARKETS" \
  --per-league \
  --max-leagues "$BACKTEST_MAX_LEAGUES" \
  --min-trainable-per-league "$BACKTEST_MIN_TRAINABLE" \
  --ev-min 0.05 \
  --confidence-min 65 \
  --bankroll 1000 \
  --profile moderado

echo "==> 7/8 Sincronizar pesquisa para Supabase, se configurado"
python scripts/sync_football_research_supabase.py --batch-size 500 || true
python scripts/sync_football_research_memory_supabase.py --batch-size 500 || true

echo "==> 8/8 Leitura final do Cérebro IA"
python - <<'PY'
import json
from services.ai_brain.brain_metrics_service import BrainMetricsService
payload = BrainMetricsService().metrics()
summary = {
    "status": payload.get("status"),
    "status_reason": payload.get("status_reason"),
    "maturity": payload.get("ia_maturity_score"),
    "maturity_label": payload.get("ia_maturity_label"),
    "metrics": {
        key: payload.get("metrics", {}).get(key)
        for key in [
            "total_jogos_analisados",
            "total_jogos_historicos",
            "total_sinais_registrados",
            "total_backtests",
            "total_simulacoes",
            "dados_com_odds_confirmadas",
            "dados_sem_odds",
            "taxa_acerto_historica",
            "ROI_simulado",
            "lucro_prejuizo_simulado",
        ]
    },
    "alerts": payload.get("alerts", [])[:6],
}
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
PY

date -u +"finished_at=%Y-%m-%dT%H:%M:%SZ"
echo "==> Pipeline concluído"
