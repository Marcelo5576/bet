#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
REPORT_FILE="${PROJECT_DIR}/diagnostics_report.txt"
APP_HOST_DEFAULT="${DOMAIN:-apexgol.com.br}"

log() {
  printf '\n== %s ==\n' "$1"
}

append_report() {
  "$@" >>"${REPORT_FILE}" 2>&1 || true
}

read_env_value() {
  python3 - "$1" "${PROJECT_DIR}/.env" <<'PY'
import sys
from pathlib import Path

key = sys.argv[1]
path = Path(sys.argv[2])
if not path.exists():
    print("")
    raise SystemExit(0)

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

main() {
  : >"${REPORT_FILE}"
  cd "${PROJECT_DIR}" || {
    printf 'Projeto não encontrado em %s\n' "${PROJECT_DIR}"
    exit 1
  }

  log "Git"
  if [[ -d .git ]]; then
    git status --short
    git rev-parse --abbrev-ref HEAD
    git rev-parse --short HEAD
    append_report git status --short
    append_report git rev-parse --abbrev-ref HEAD
    append_report git rev-parse --short HEAD
  else
    printf 'Sem repositório Git configurado.\n'
  fi

  log "Docker Compose"
  docker compose ps || true
  append_report docker compose ps

  log "Logs recentes dashboard"
  docker compose logs --tail=80 dashboard || true
  append_report docker compose logs --tail=80 dashboard

  log "Logs recentes betsignal"
  docker compose logs --tail=80 betsignal || true
  append_report docker compose logs --tail=80 betsignal

  log "Logs recentes caddy"
  docker compose logs --tail=80 caddy || true
  append_report docker compose logs --tail=80 caddy

  log "Caddyfile montado no container"
  docker compose exec -T caddy sh -lc 'cat /etc/caddy/Caddyfile' || true
  append_report docker compose exec -T caddy sh -lc 'cat /etc/caddy/Caddyfile'

  log "Validação do Caddy"
  docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile || true
  append_report docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile

  log "Uso de disco"
  df -h
  append_report df -h

  log "Uso de memória"
  free -h || true
  append_report free -h

  log "Erros 429"
  docker compose logs --tail=300 dashboard betsignal 2>/dev/null | grep -i "429" || printf 'Nenhum 429 recente encontrado.\n'
  {
    docker compose logs --tail=300 dashboard betsignal 2>/dev/null | grep -i "429"
  } >>"${REPORT_FILE}" 2>&1 || true

  log "Supabase"
  if docker compose ps --services --filter status=running | grep -q '^dashboard$'; then
    docker compose exec -T dashboard python scripts/validate_supabase_connection.py || true
    append_report docker compose exec -T dashboard python scripts/validate_supabase_connection.py
  else
    printf 'Dashboard container não está ativo.\n'
  fi

  log "Football provider"
  if docker compose ps --services --filter status=running | grep -q '^dashboard$'; then
    docker compose exec -T dashboard python - <<'PY' || true
import json
from src.config import load_settings
from src.dashboard import _football_api_provider

settings = load_settings()
provider = _football_api_provider(settings)
print(json.dumps({
    "configured": bool(settings.api_football_key),
    "base_url": settings.api_football_base_url,
    "status": provider.status_snapshot(),
}, ensure_ascii=False, indent=2))
PY
    append_report docker compose exec -T dashboard python -c "from src.config import load_settings; from src.dashboard import _football_api_provider; import json; s=load_settings(); p=_football_api_provider(s); print(json.dumps({'configured': bool(s.api_football_key), 'base_url': s.api_football_base_url, 'status': p.status_snapshot()}, ensure_ascii=False, indent=2))"
  else
    printf 'Dashboard container não está ativo.\n'
  fi

  log "HTTP local"
  APP_URL="$(read_env_value APP_URL)"
  HOST_NAME="$(python3 - "${APP_URL:-}" <<'PY'
import sys
from urllib.parse import urlparse

raw = sys.argv[1] or "https://apexgol.com.br"
parsed = urlparse(raw if "://" in raw else f"https://{raw}")
print(parsed.netloc or "apexgol.com.br")
PY
)"
  HOST_NAME="${HOST_NAME:-$APP_HOST_DEFAULT}"
  curl -I -H "Host: ${HOST_NAME}" http://127.0.0.1/ || true
  append_report curl -I -H "Host: ${HOST_NAME}" http://127.0.0.1/

  log "Último deploy"
  if [[ -f "${PROJECT_DIR}/deploy_report.txt" ]]; then
    tail -n 40 "${PROJECT_DIR}/deploy_report.txt"
    tail -n 40 "${PROJECT_DIR}/deploy_report.txt" >>"${REPORT_FILE}"
  else
    printf 'deploy_report.txt ainda não existe.\n'
  fi

  log "Relatório salvo"
  printf '%s\n' "${REPORT_FILE}"
}

main "$@"
