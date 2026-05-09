#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
BRANCH="${BRANCH:-main}"
APP_HOST_DEFAULT="${DOMAIN:-apexgol.com.br}"
BACKUP_ROOT="${BACKUP_ROOT:-${HOME}/backups/betsignal}"
REPORT_FILE="${PROJECT_DIR}/deploy_report.txt"
STAMP="$(date +%Y%m%d-%H%M%S)"

required_items=(
  ".env"
  "docker-compose.yml"
  "Dockerfile"
  "requirements.txt"
  "src/dashboard.py"
  "src/main.py"
  "src/portal_web.py"
  "src/ai_brain_router.py"
  "scripts/smoke_install.py"
  "scripts/apply_local_migrations.py"
  "scripts/validate_supabase_connection.py"
)

REPORT_LINES=()

log() {
  printf '==> %s\n' "$1"
}

note() {
  REPORT_LINES+=("$1")
}

fail() {
  printf 'ERRO: %s\n' "$1" >&2
  note "ERRO: $1"
  write_report || true
  exit 1
}

ensure_project() {
  [[ -d "${PROJECT_DIR}" ]] || fail "Projeto não encontrado em ${PROJECT_DIR}"
  cd "${PROJECT_DIR}"
  for item in "${required_items[@]}"; do
    [[ -e "${item}" ]] || fail "Arquivo obrigatório ausente: ${item}"
  done
}

require_tools() {
  command -v python3 >/dev/null 2>&1 || fail "python3 não instalado."
  command -v docker >/dev/null 2>&1 || fail "Docker não instalado."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin não disponível."
}

backup_current_state() {
  local backup_dir
  backup_dir="${BACKUP_ROOT}/deploy-backup-${STAMP}"
  mkdir -p "${BACKUP_ROOT}"
  log "Criando backup em ${backup_dir}"
  cp -a "${PROJECT_DIR}" "${backup_dir}"
  note "Backup: ${backup_dir}"
}

git_update_if_available() {
  if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
    log "Projeto sem .git. Pulando git pull."
    note "Git: nao configurado no servidor"
    return
  fi

  log "Atualizando repositório Git"
  git fetch origin "${BRANCH}"
  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    git checkout "${BRANCH}"
  else
    git checkout -b "${BRANCH}" "origin/${BRANCH}"
  fi
  git pull --ff-only origin "${BRANCH}"
  note "Git: $(git rev-parse --short HEAD) em ${BRANCH}"
}

read_env_value() {
  python3 - "$1" ".env" <<'PY'
import sys
from pathlib import Path

key = sys.argv[1]
path = Path(sys.argv[2])
value = ""
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, raw = line.split("=", 1)
    if name.strip() == key:
        value = raw.strip()
        break
print(value)
PY
}

validate_env() {
  python3 scripts/normalize_domain_env.py ".env" "${APP_HOST_DEFAULT}" >/dev/null
  local missing
  local app_env football_api_key
  missing="$(python3 - ".env" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
required = {
    "APP_URL": {"", "https://example.com"},
    "ADMIN_EMAIL": {"", "admin@betsignal.local"},
    "ADMIN_PASSWORD": {"", "change-me-now"},
    "ADMIN_NAME": {""},
    "DASHBOARD_USER": {""},
    "DASHBOARD_PASSWORD": {"", "change-me-now"},
    "PORTAL_SESSION_SECRET": {"", "change-this-session-secret", "change-me-now"},
}

values = {}
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

missing = []
for key, invalid in required.items():
    value = values.get(key, "")
    if value in invalid:
        missing.append(key)
print("\n".join(missing))
PY
)"
  if [[ -n "${missing}" ]]; then
    fail "Variáveis obrigatórias pendentes no .env: ${missing//$'\n'/, }"
  fi
  app_env="$(read_env_value APP_ENV)"
  football_api_key="$(
    read_env_value API_FOOTBALL_KEY
    printf '%s' "$(read_env_value API_SPORTS_KEY)"
    printf '%s' "$(read_env_value APIFOOTBALL_KEY)"
    printf '%s' "$(read_env_value API_FOOTBALL_TOKEN)"
  )"
  if [[ -z "${football_api_key}" ]]; then
    if [[ "${app_env,,}" == "production" ]]; then
      fail "API_FOOTBALL_KEY ausente no .env (aliases aceitos: API_SPORTS_KEY, APIFOOTBALL_KEY, API_FOOTBALL_TOKEN). O dominio principal ficara sem dados ao vivo."
    fi
    note "Football API: chave ausente (modo fallback/local)"
  else
    note "Football API: chave presente no .env"
  fi
  note "Env: validado"
}

validate_python_syntax() {
  log "Validando sintaxe Python no host"
  python3 -m compileall -q src services scripts
  note "Python: compileall host OK"
}

build_and_up() {
  log "Buildando containers"
  docker compose build
  log "Subindo containers"
  docker compose up -d --force-recreate
  note "Docker: build e up concluidos"
}

run_container_checks() {
  log "Aplicando migrations locais"
  docker compose exec -T dashboard python scripts/apply_local_migrations.py

  log "Validando conexão Supabase"
  if ! docker compose exec -T dashboard python scripts/validate_supabase_connection.py; then
    printf 'AVISO: Supabase configurado com erro. O sistema continua em modo local.\n'
    note "Supabase: aviso durante validacao"
  else
    note "Supabase: validacao OK"
  fi

  log "Validando sintaxe Python no container"
  docker compose exec -T dashboard python -m compileall -q src services scripts

  log "Executando smoke tests principais"
  docker compose exec -T dashboard python scripts/smoke_install.py

  if [[ -f scripts/smoke_saas_identity.py ]]; then
    log "Executando smoke da landing e identidade"
    docker compose exec -T dashboard python scripts/smoke_saas_identity.py
  fi

  if [[ -f scripts/smoke_quant_markets.py ]]; then
    log "Executando smoke dos mercados quantitativos"
    docker compose exec -T dashboard python scripts/smoke_quant_markets.py
  fi

  note "Smokes: instalacao, identidade e mercados OK"
}

validate_football_provider_env() {
  local provider_payload
  log "Validando provider Football no container"
  provider_payload="$(docker compose exec -T dashboard python - <<'PY'
import json
from src.config import load_settings

settings = load_settings()
print(json.dumps({
    "configured": bool(settings.api_football_key),
    "base_url": settings.api_football_base_url,
}, ensure_ascii=False))
PY
)"
  printf '%s\n' "${provider_payload}"
  if ! grep -q '"configured": true' <<<"${provider_payload}"; then
    fail "O container dashboard subiu sem API_FOOTBALL_KEY. Corrija o .env e rode o deploy novamente."
  fi
  note "Football API container env: OK"
}

http_fetch() {
  local out_file
  out_file="$1"
  shift
  curl -fsS -L "$@" -o "${out_file}"
}

http_smoke() {
  local app_url app_host app_host_only dashboard_user dashboard_password
  app_url="$(read_env_value APP_URL)"
  app_host="$(python3 - "${app_url:-}" <<'PY'
import sys
from urllib.parse import urlparse

raw = sys.argv[1] or "https://apexgol.com.br"
parsed = urlparse(raw if "://" in raw else f"https://{raw}")
print(parsed.netloc or "apexgol.com.br")
PY
)"
  app_host="${app_host:-$APP_HOST_DEFAULT}"
  app_host_only="$(python3 - "${app_host}" <<'PY'
import sys

raw = (sys.argv[1] or "apexgol.com.br").strip()
print(raw.split(":", 1)[0] or "apexgol.com.br")
PY
)"
  dashboard_user="$(read_env_value DASHBOARD_USER)"
  dashboard_password="$(read_env_value DASHBOARD_PASSWORD)"

  log "Validando HTTP via Caddy"
  curl -fsS -I -H "Host: ${app_host}" "http://127.0.0.1/" >/tmp/apexgol_http_headers.txt
  grep -qi "Location: https://${app_host_only}/" /tmp/apexgol_http_headers.txt || fail "Caddy nao redirecionou HTTP para HTTPS como esperado."

  http_fetch /tmp/apexgol_landing.html --noproxy "*" -k --resolve "${app_host_only}:443:127.0.0.1" "https://${app_host_only}/"
  http_fetch /tmp/apexgol_login.html --noproxy "*" -k --resolve "${app_host_only}:443:127.0.0.1" "https://${app_host_only}/login"
  http_fetch /tmp/apexgol_dashboard.html --noproxy "*" -k --resolve "${app_host_only}:443:127.0.0.1" -u "${dashboard_user}:${dashboard_password}" "https://${app_host_only}/dashboard"
  http_fetch /tmp/apexgol_rate_limit.json --noproxy "*" -k --resolve "${app_host_only}:443:127.0.0.1" -u "${dashboard_user}:${dashboard_password}" "https://${app_host_only}/api/system/rate-limit-protection"

  for legacy in "Fantasy" "Jogos do Dia" "Live Center" "ENTRA_FORTE" "Decision class"; do
    if grep -q "${legacy}" /tmp/apexgol_landing.html; then
      fail "Landing ainda contém texto legado: ${legacy}"
    fi
  done

  grep -qi "ApexGol AI" /tmp/apexgol_landing.html || fail "Landing não carregou a identidade ApexGol AI."
  grep -q "rate_limit_protection" /tmp/apexgol_rate_limit.json || fail "API de rate limit protection não respondeu corretamente."

  note "HTTP: landing/login/dashboard/api OK"
}

write_report() {
  {
    printf 'ApexGol AI - Deploy Report\n'
    printf 'Timestamp: %s\n' "$(date -Is)"
    printf 'Project Dir: %s\n' "${PROJECT_DIR}"
    printf 'Branch: %s\n' "${BRANCH}"
    printf 'User: %s\n' "$(id -un)"
    if [[ -d .git ]]; then
      printf 'Git Commit: %s\n' "$(git rev-parse --short HEAD)"
    else
      printf 'Git Commit: nao aplicavel\n'
    fi
    printf 'Checks:\n'
    if [[ "${#REPORT_LINES[@]}" -eq 0 ]]; then
      printf -- '- sem observacoes\n'
    else
      for line in "${REPORT_LINES[@]}"; do
        printf -- '- %s\n' "${line}"
      done
    fi
    printf 'URLs:\n'
    printf -- '- %s/\n' "$(read_env_value APP_URL)"
    printf -- '- %s/login\n' "$(read_env_value APP_URL)"
    printf -- '- %s/dashboard\n' "$(read_env_value APP_URL)"
    printf -- '- %s/cerebro-ia\n' "$(read_env_value APP_URL)"
  } >"${REPORT_FILE}"
}

show_status() {
  local app_url
  app_url="$(read_env_value APP_URL)"
  write_report
  log "Status final"
  docker compose ps
  printf 'Landing: %s/\n' "${app_url:-https://apexgol.com.br}"
  printf 'Login: %s/login\n' "${app_url:-https://apexgol.com.br}"
  printf 'Dashboard: %s/dashboard\n' "${app_url:-https://apexgol.com.br}"
  printf 'Cerebro IA: %s/cerebro-ia\n' "${app_url:-https://apexgol.com.br}"
  printf 'Deploy report: %s\n' "${REPORT_FILE}"
}

main() {
  ensure_project
  require_tools
  backup_current_state
  git_update_if_available
  validate_env
  validate_python_syntax
  build_and_up
  run_container_checks
  validate_football_provider_env
  http_smoke
  show_status
}

main "$@"
