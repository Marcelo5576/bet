# Global Adaptive Sports & Market Intelligence Platform

## Auditoria do que já existia

### Backend
- Python + FastAPI em `src/dashboard.py`, `src/main.py`, `src/portal_web.py`
- scanner ao vivo, Telegram, paper trading, football brain e integração Supabase por REST
- sem Node, sem React, sem Prisma, sem package.json

### Frontend
- HTML/CSS/JS renderizados pelo backend
- páginas já existentes de dashboard, área do cliente, fantasy e jogos do dia

### Banco
- SQLite local:
  - `data/portal.db`
  - `data/state.json`
  - `data/football_brain.db`
- Supabase com esquema inicial em `supabase_schema.sql`

### Autenticação
- multiusuário local com cookie de sessão assinado
- admin local em `portal.db`
- sem auth Supabase/RLS ativo hoje

### Módulos reutilizados
- `src/intelligence/football_brain.py`
- `src/intelligence/risk.py`
- `src/intelligence/scoring.py`
- `src/integrations/supabase.py`
- `services/footballQuantAiSkill/*`

## O que foi criado

- `services/globalAdaptiveIntelligence/*`
- `src/global_ai_router.py`
- migrations reversíveis em `migrations/`
- testes Python em `tests/football_quant_ai/`

## Como usar

### Páginas novas
- `/app/global-ai-control-center`
- `/app/football-analysis`
- `/app/backtesting-lab`
- `/app/monte-carlo-lab`
- `/app/strategy-evolution-lab`
- `/app/agent-arena`
- `/app/feature-lab`
- `/app/drift-regime-monitor`
- `/app/market-bias-anomaly-center`
- `/app/rag-memory-explorer`
- `/app/governance-center`

### APIs novas
- `/api/global-ai/audit`
- `/api/global-ai/control-center`
- `/api/global-ai/football-analysis`
- `/api/global-ai/football-analysis/event`
- `/api/global-ai/backtest`
- `/api/global-ai/monte-carlo`
- `/api/global-ai/strategy-evolution`
- `/api/global-ai/agent-arena`
- `/api/global-ai/feature-lab`
- `/api/global-ai/drift-regime`
- `/api/global-ai/bias-anomaly`
- `/api/global-ai/rag-query`
- `/api/global-ai/governance`

## Variáveis de ambiente

- `FOOTBALL_RESEARCH_DB_FILE`
- `FOOTBALL_RESEARCH_CSV_ROOT`
- `FOOTBALL_RESEARCH_MOCKS_ENABLED`
- `FOOTBALL_RESEARCH_DEFAULT_BANKROLL`
- `FOOTBALL_RESEARCH_DEFAULT_PROFILE`
- `FOOTBALL_RESEARCH_MIN_EV`
- `FOOTBALL_RESEARCH_MIN_CONFIDENCE`
- `FOOTBALL_RESEARCH_AUTO_SEED_MOCKS`
- `STATSBOMB_OPEN_BASE_URL`
- `GLOBAL_AI_DB_FILE`
- `GLOBAL_AI_DEFAULT_SPORT`
- `GLOBAL_AI_DEFAULT_MARKET`
- `GLOBAL_AI_MOCKS_ENABLED`
- `GLOBAL_AI_MIN_CONFIDENCE`
- `GLOBAL_AI_MIN_EV`
- `GLOBAL_AI_MONTE_CARLO_PATHS`
- `GLOBAL_AI_MONTE_CARLO_STEPS`
- `GLOBAL_AI_GOVERNANCE_AUTO_DRAFT`

## Limites atuais

- futebol implementado primeiro; crypto/finance preparados como interface e registry, não como execução completa
- Supabase recebe migration SQL, mas a aplicação atual continua segura mesmo sem aplicação da migration
- o frontend segue o padrão atual server-rendered para não introduzir uma segunda stack

## Aviso

Este sistema é apenas uma ferramenta estatística de apoio. Não garante lucro. Use com responsabilidade.

