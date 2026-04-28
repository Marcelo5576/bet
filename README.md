# BetSignal Cloud

Robo Telegram para sinais de futebol ao vivo, com modo teste sem API paga,
API-Football opcional, Gemini opcional, controle de jogo ativo e persistencia
em `data/state.json`.

## Comandos

- `/start` abre o menu.
- `/scan` procura jogos ao vivo agora.
- `/status` mostra o jogo ativo e o proximo scan.
- `/stop` libera o jogo ativo e permite novo scan.
- `/oferta` mostra a oferta comercial e planos.
- `/chatid` mostra o ID do chat para vincular notificacoes por usuario no portal.

## Gestao de risco

O bot nao executa apostas automaticamente. Ele envia alerta informativo com
acao, confianca e unidade sugerida para decisao manual.

- `BANKROLL`: banca usada para calcular a unidade.
- `UNIT_PERCENT`: tamanho de 1 unidade em percentual da banca.
- `MAX_STAKE_UNITS`: limite de unidades por sinal.
- `MIN_HISTORY_FOR_ENTER`: amostra minima antes de permitir `ENTRAR`.
- `MIN_EDGE_TO_ENTER`: edge minimo para entrada.
- `KELLY_FRACTION`: fracao conservadora do Kelly para stake.
- `DAILY_RED_LIMIT`: limite de referencia para alerta de disciplina (nao bloqueia scanner).

Quando o limite de reds e atingido, o sistema envia alerta de disciplina
e recomenda reduzir risco/revisar criterio, mas sem travar o scanner.

## Produto vendavel

Voce pode configurar nome comercial, canais de venda e planos no `.env`:

- `PRODUCT_NAME`
- `PRODUCT_TAGLINE`
- `WEBSITE_URL`
- `SALES_WHATSAPP`
- `SALES_EMAIL`
- `PLAN_STARTER_PRICE_BRL`
- `PLAN_PRO_PRICE_BRL`
- `PLAN_TEAM_PRICE_BRL`

Materiais de comercializacao no repositorio:

- `PRODUCT_OFFER_BR.md`
- `SALES_PLAYBOOK_BR.md`
- `LAUNCH_30_DIAS_BR.md`

## SaaS (cadastro, login e cobranca)

Rotas principais do novo portal:

- `/` landing page comercial com oferta e CTA.
- `/signup` cadastro com teste gratis de `PORTAL_TRIAL_DAYS` dias.
- `/login` login do cliente.
- `/forgot-password` e `/reset-password` com fluxo por email.
- `/app` area do cliente com billing e agente de suporte.
- `/admin/users` painel admin para cobrar, cancelar, reativar e gerar checkout.
- `/dashboard` dashboard operacional do trade (protegida por Basic Auth).

Variaveis novas no `.env`:

- `PORTAL_DB_FILE`, `PORTAL_SESSION_SECRET`, `PORTAL_SESSION_HOURS`
- `PORTAL_TRIAL_DAYS`, `ADMIN_EMAIL`, `ADMIN_NAME`, `ADMIN_PASSWORD`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`
- `PAYMENT_GATEWAY` (`stripe` ou `mercadopago`)
- `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_TEAM`
- `MERCADOPAGO_ACCESS_TOKEN`

## Seguranca cibernetica aplicada

- Cookies de sessao `HttpOnly` + `Secure` (quando HTTPS) + `SameSite=Lax`.
- Token de sessao assinado com HMAC.
- Headers de seguranca reforcados (`CSP`, `COOP`, `CORP`, `HSTS` em HTTPS).
- Rate limit basico para login/cadastro/reset.
- Rotas admin protegidas por perfil `is_admin`.
- Mensagem neutra no "esqueci senha" para evitar enumeracao de emails.

## Scanner e Telegram por usuario

- Ciclo padrao sem jogo ativo: 1 minuto.
- Ciclo com jogo ativo: 5 minutos.
- Cada usuario pode ajustar preferencia no portal (`/app`) e optar por notificacao Telegram.
- Guia completo: `TELEGRAM_CONNECT_GUIDE_BR.md`.

## Simulacao diaria automatica da IA

- O bot roda simulacao paper/live automaticamente todo dia e salva no historico da dashboard.
- A simulacao usa o feed atual de jogos ao vivo e oportunidades do scanner no momento da execucao.
- Variaveis no `.env`:
  - `AUTO_SIMULATION_ENABLED` (`true`/`false`)
  - `AUTO_SIMULATION_HOUR` (0-23, horario local da timezone configurada)
  - `AUTO_SIMULATION_TIMEZONE` (ex.: `America/Sao_Paulo`)
  - `AUTO_SIMULATION_GAMES` (minimo 30, maximo 120)
  - `AUTO_SIMULATION_BANKROLL` (banca paper)
  - `AUTO_SIMULATION_STAKE_PERCENT` (% de stake por jogada simulada)

## Rodar localmente

```bash
cp .env.example .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m src.main
```

## Rodar com Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs -f betsignal
```

## Dashboard

O servico `dashboard` abre a porta 80 e mostra historico, greens, reds,
taxa de acerto e leitura estatistica para os proximos jogos.

```bash
docker compose logs -f dashboard
```

Configure `DASHBOARD_USER` e `DASHBOARD_PASSWORD` no `.env`.
Use `DASHBOARD_DOMAINS` para listar os acessos mostrados na dashboard e no
Telegram.

Comandos uteis no Telegram:

- `/dashboard` mostra os links.
- `/stats` mostra eficiencia e aprendizado.
- `/suporte` gera diagnostico para compartilhar no Codex.

Preencha `TELEGRAM_BOT_TOKEN` no `.env` para o bot conectar ao Telegram.
Preencha `API_FOOTBALL_KEY` para jogos ao vivo reais e odds ao vivo via
API-Football. Sem essa chave, o bot usa o provider publico da ESPN como
fallback real para placares, estatisticas e odds quando disponiveis. Use
`TEST_MODE=true` somente quando quiser dados mockados.
