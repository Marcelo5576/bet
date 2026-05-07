# ApexGol AI - instalação por Git e deploy permanente

Este fluxo deixa o ApexGol AI instalável e atualizável direto do repositório, sem depender de ZIP manual.

## Primeira instalação em servidor limpo

1. Formate o servidor Ubuntu ou Debian.
2. Acesse por console ou SSH.
3. Faça o seed inicial do repositório:

```bash
apt-get update -y
apt-get install -y git
git clone https://github.com/USUARIO/apexgol-ai.git /root/apexgol-bootstrap
cd /root/apexgol-bootstrap
chmod +x server_bootstrap_apexgol.sh
```

4. Rode o bootstrap apontando para o repositório:

```bash
REPO_URL=https://github.com/USUARIO/apexgol-ai.git \
BRANCH=main \
DOMAIN=novo.tickpost.com.br \
./server_bootstrap_apexgol.sh
```

5. Edite o arquivo `/opt/betsignal-cloud/.env` se o script apontar variáveis pendentes:

```bash
nano /opt/betsignal-cloud/.env
```

6. Rode o deploy:

```bash
cd /opt/betsignal-cloud
./deploy_apexgol.sh
```

7. Abra o domínio:

```text
https://novo.tickpost.com.br
```

## Deploy futuro sem ZIP

Quando o repositório já estiver no servidor:

```bash
cd /opt/betsignal-cloud
./deploy_apexgol.sh
```

Esse script faz backup, atualiza via Git, rebuilda os containers, roda smoke tests e gera `deploy_report.txt`.

## Scripts principais

`server_bootstrap_apexgol.sh`

- instala dependências do servidor
- clona ou atualiza o repositório
- preserva `.env` antigo
- chama `install_apexgol.sh`
- orienta edição do `.env`
- chama `deploy_apexgol.sh`
- gera `install_report.txt`

`install_apexgol.sh`

- valida Ubuntu ou Debian
- instala Docker, Compose, Python, Git e utilitários
- copia ou prepara o projeto em `/opt/betsignal-cloud`
- cria `.env` a partir de `.env.example` se necessário
- preserva `.env` existente
- roda deploy automático se o `.env` já estiver válido

`deploy_apexgol.sh`

- cria backup antes de atualizar
- roda `git pull` quando existe `.git`
- valida `.env`
- valida sintaxe Python
- executa migrations locais
- sobe `dashboard`, `betsignal` e `caddy`
- roda smoke tests:
  - `/`
  - `/login`
  - `/dashboard`
  - `/cerebro-ia`
  - `/api/system/rate-limit-protection`
- garante que a landing não contém:
  - `Fantasy`
  - `Jogos do Dia`
  - `Live Center`
  - `ENTRA_FORTE`
  - `Decision class`
- gera `deploy_report.txt`

`rollback_apexgol.sh`

- restaura um backup anterior
- recria containers
- roda validação básica

`diagnostics.sh`

- mostra estado do Docker
- mostra logs recentes
- mostra uso de disco e memória
- mostra status Git
- mostra erros 429 recentes
- mostra o último relatório de deploy

`create_deploy_user.sh`

- cria o usuário `deploy`
- instala chave pública SSH
- opcionalmente entrega posse do projeto ao usuário
- prepara sudo restrito

## Variáveis do bootstrap

O bootstrap aceita:

- `REPO_URL`
- `BRANCH` com padrão `main`
- `PROJECT_DIR` com padrão `/opt/betsignal-cloud`
- `DOMAIN` com padrão `novo.tickpost.com.br`

Exemplo:

```bash
REPO_URL=https://github.com/USUARIO/apexgol-ai.git \
BRANCH=main \
PROJECT_DIR=/opt/betsignal-cloud \
DOMAIN=novo.tickpost.com.br \
bash server_bootstrap_apexgol.sh
```

## Variáveis importantes do `.env`

Obrigatórias:

- `APP_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ADMIN_NAME`
- `DASHBOARD_USER`
- `DASHBOARD_PASSWORD`
- `PORTAL_SESSION_SECRET`

Opcionais e recomendadas:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `API_FOOTBALL_KEY`
- `THE_ODDS_API_KEY`
- `ISPORTS_API_KEY`
- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_CHAT_ID`
- `TELEGRAM_GROUP_CHAT_ID`

Se o Supabase não estiver configurado, o sistema segue em modo local.

## Troubleshooting

`python3 não encontrado`

```bash
apt-get update -y && apt-get install -y python3 python3-venv
```

`docker não instalado`

```bash
apt-get install -y docker.io docker-compose-plugin
systemctl enable --now docker
```

`porta 80/443 ocupada`

```bash
ss -tulpn | grep -E ':80|:443'
```

`Supabase 401`

- revise `SUPABASE_URL`
- revise `SUPABASE_SERVICE_ROLE_KEY`

`API 429`

- revise os limites dos providers
- rode `./diagnostics.sh`

`Caddy não sobe`

```bash
docker compose logs --tail=200 caddy
```

`deploy não atualiza`

- confira o branch configurado
- rode `git status`
- abra `deploy_report.txt`
- rode `./diagnostics.sh`

## Observações

- `.env` nunca é commitado
- `.env` nunca é sobrescrito sem backup
- a instalação anterior nunca é apagada sem backup
- o fluxo não depende de upload manual por ZIP
- o sistema não automatiza apostas reais
