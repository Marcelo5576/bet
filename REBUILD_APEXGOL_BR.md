# Rebuild Seguro do ApexGol

Este guia existe para evitar dois problemas:

1. subir codigo novo e perder estado sem perceber;
2. zerar o projeto inteiro quando o defeito esta so no runtime.

## 1. Auditar antes de mexer

No projeto local:

```bash
python scripts/audit_runtime.py
```

Se quiser o relatorio em JSON:

```bash
python scripts/audit_runtime.py --json
```

O auditor mostra:

- commit atual do projeto;
- situacao do `data/state.json`;
- situacao do `data/portal.db`;
- usuarios admin;
- tamanho da amostra de aprendizado ainda visivel;
- se Telegram, Supabase, Gemini e API-Football estao configurados.

## 2. Reset controlado do runtime

Para limpar historico operacional, sinais ativos e jogos recentes, mas manter
usuarios do portal:

```bash
python scripts/reset_runtime.py
```

Esse comando:

- cria backup automatico em `backups/runtime/<timestamp>/data`;
- recria `data/state.json`;
- zera historico, jogo ativo, candidatos e jogos recentes;
- preserva `chat_ids` e `scan_preference` por padrao.

### Variantes uteis

Resetar tudo e tambem recriar o banco do portal:

```bash
python scripts/reset_runtime.py --wipe-portal-db
```

Resetar sem preservar chat IDs:

```bash
python scripts/reset_runtime.py --drop-chat-ids
```

Resetar mas manter as simulacoes antigas:

```bash
python scripts/reset_runtime.py --preserve-simulations
```

## 3. Publicar no servidor

Fluxo recomendado:

### Na maquina local

```bash
git add .
git commit -m "nova atualizacao"
git push
```

### Na VPS (terminal Hostinger)

```bash
cd /opt/betsignal-cloud
git pull origin main
docker compose build dashboard betsignal
docker compose up -d --force-recreate dashboard betsignal caddy
docker compose ps
```

Se o helper existir na VPS:

```bash
deploy-apexgol.sh
```

## 3.1. Religar modo real e semear o runtime

Depois do reset, rode:

```bash
python scripts/prime_real_mode.py --seed-state --simulate-now
```

Esse comando:

- usa o provider real configurado no `.env`;
- busca jogos do feed ao vivo;
- repovoa `last_games` e `candidate_signals`;
- roda uma simulacao imediata com o feed real atual;
- mostra os bloqueios restantes, como Telegram, Gemini, API-Football e Supabase.

## 4. Restaurar admin da plataforma

No servidor, o `.env` principal fica em `/opt/.env`. Se o admin perder acesso:

```bash
grep -q '^ADMIN_EMAIL=' /opt/.env && sed -i 's|^ADMIN_EMAIL=.*|ADMIN_EMAIL=ensgra@gmail.com|' /opt/.env || echo 'ADMIN_EMAIL=ensgra@gmail.com' >> /opt/.env
grep -q '^ADMIN_NAME=' /opt/.env && sed -i 's|^ADMIN_NAME=.*|ADMIN_NAME=Administrador ApexGol|' /opt/.env || echo 'ADMIN_NAME=Administrador ApexGol' >> /opt/.env
grep -q '^ADMIN_PASSWORD=' /opt/.env && sed -i 's|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=ApexGol!SuperAdmin#2026@VX47|' /opt/.env || echo 'ADMIN_PASSWORD=ApexGol!SuperAdmin#2026@VX47' >> /opt/.env

cd /opt/betsignal-cloud
docker compose up -d --force-recreate dashboard betsignal
```

## 5. Reconstruir aprendizado da IA

Se o `history` real estiver vazio, a ordem certa e:

1. importar resultados reais pela dashboard;
2. sincronizar com Supabase, se configurado;
3. reativar simulacao diaria real;
4. acompanhar `sample_size`, `real_sample_size` e `simulation_sample_size`.

O que acelera de verdade:

- `TEST_MODE=false`;
- feed real ativo;
- encerramento correto de green/red;
- imports reais consistentes;
- Supabase com `supabase_schema.sql` aplicado.

## 6. O que nao fazer

- nao apagar `data/` sem backup;
- nao subir `.env` para o Git;
- nao confiar so em simulacao como historico final;
- nao publicar direto no servidor sem conferir o commit atual.
