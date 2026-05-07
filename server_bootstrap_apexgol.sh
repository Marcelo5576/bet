#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-main}"
PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
DOMAIN="${DOMAIN:-novo.tickpost.com.br}"
BACKUP_ROOT="${BACKUP_ROOT:-/root/backups/betsignal}"
STAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="${PROJECT_DIR}/install_report.txt"
SKIP_SYSTEM_PACKAGES="${SKIP_SYSTEM_PACKAGES:-false}"

log() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERRO: %s\n' "$1" >&2
  exit 1
}

require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Execute como root."
}

validate_os() {
  [[ -f /etc/os-release ]] || fail "/etc/os-release não encontrado."
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) fail "Sistema não suportado: ${ID:-desconhecido}. Use Ubuntu ou Debian." ;;
  esac
}

install_dependencies() {
  if [[ "${SKIP_SYSTEM_PACKAGES}" == "true" ]]; then
    log "SKIP_SYSTEM_PACKAGES=true. Pulando instalação de pacotes do sistema."
    return
  fi

  export DEBIAN_FRONTEND=noninteractive
  log "Instalando dependências base do servidor"
  apt-get update -y
  apt-get install -y \
    ca-certificates \
    curl \
    git \
    nano \
    unzip \
    python3 \
    python3-venv

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker e Docker Compose já estão disponíveis. Reutilizando instalação atual."
  else
    apt-get install -y docker.io docker-compose-plugin
    systemctl enable --now docker
  fi
}

backup_existing_dir() {
  if [[ -d "${PROJECT_DIR}" ]]; then
    mkdir -p "${BACKUP_ROOT}"
    local backup_dir="${BACKUP_ROOT}/bootstrap-backup-${STAMP}"
    log "Criando backup de ${PROJECT_DIR} em ${backup_dir}"
    cp -a "${PROJECT_DIR}" "${backup_dir}"
  fi
}

clone_or_update_repo() {
  [[ -n "${REPO_URL}" ]] || fail "Defina REPO_URL para rodar o bootstrap."

  if [[ -d "${PROJECT_DIR}/.git" ]]; then
    log "Repositorio Git existente detectado. Atualizando branch ${BRANCH}"
    cd "${PROJECT_DIR}"
    git fetch origin "${BRANCH}"
    if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
      git checkout "${BRANCH}"
    else
      git checkout -b "${BRANCH}" "origin/${BRANCH}"
    fi
    git pull --ff-only origin "${BRANCH}"
    return
  fi

  if [[ -d "${PROJECT_DIR}" ]]; then
    log "Diretorio existente sem Git. Recriando a partir do repositório."
    rm -rf "${PROJECT_DIR}"
  fi

  log "Clonando ${REPO_URL} em ${PROJECT_DIR}"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${PROJECT_DIR}"
}

ensure_env_file() {
  cd "${PROJECT_DIR}"
  if [[ ! -f .env ]]; then
    log "Criando .env a partir de .env.example"
    cp .env.example .env
  fi

  python3 - ".env" "${DOMAIN}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
domain = sys.argv[2].strip()
if not domain:
    raise SystemExit(0)

lines = path.read_text(encoding="utf-8").splitlines()
updated = []
found = False
target = f"APP_URL=https://{domain}"
for line in lines:
    if line.startswith("APP_URL="):
      updated.append(target)
      found = True
    else:
      updated.append(line)
if not found:
    updated.append(target)
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
}

ensure_permissions() {
  cd "${PROJECT_DIR}"
  chmod +x \
    install_apexgol.sh \
    deploy_apexgol.sh \
    rollback_apexgol.sh \
    diagnostics.sh \
    server_bootstrap_apexgol.sh \
    create_deploy_user.sh
}

list_missing_env() {
  cd "${PROJECT_DIR}"
  python3 - ".env" <<'PY'
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
}

run_install() {
  log "Rodando instalador do projeto"
  cd "${PROJECT_DIR}"
  PROJECT_DIR="${PROJECT_DIR}" BACKUP_ROOT="${BACKUP_ROOT}" AUTO_DEPLOY=false SKIP_SYSTEM_PACKAGES="${SKIP_SYSTEM_PACKAGES}" ./install_apexgol.sh
}

edit_env_if_needed() {
  local missing
  missing="$(list_missing_env || true)"
  if [[ -z "${missing}" ]]; then
    return
  fi

  printf 'Edite agora o arquivo %s/.env.\nVariáveis pendentes:\n%s\n' "${PROJECT_DIR}" "${missing}"
  if command -v nano >/dev/null 2>&1; then
    nano "${PROJECT_DIR}/.env"
  fi

  missing="$(list_missing_env || true)"
  [[ -z "${missing}" ]] || fail "Ainda faltam variáveis obrigatórias no .env: ${missing//$'\n'/, }"
}

run_deploy() {
  log "Rodando deploy final"
  cd "${PROJECT_DIR}"
  BRANCH="${BRANCH}" PROJECT_DIR="${PROJECT_DIR}" ./deploy_apexgol.sh
}

write_report() {
  mkdir -p "${PROJECT_DIR}"
  {
    printf 'ApexGol AI - Bootstrap Report\n'
    printf 'Timestamp: %s\n' "$(date -Is)"
    printf 'Repo URL: %s\n' "${REPO_URL}"
    printf 'Branch: %s\n' "${BRANCH}"
    printf 'Project Dir: %s\n' "${PROJECT_DIR}"
    printf 'Domain: %s\n' "${DOMAIN}"
    printf 'Next Step: cd %s && ./deploy_apexgol.sh\n' "${PROJECT_DIR}"
  } >>"${REPORT_FILE}"
}

main() {
  require_root
  validate_os
  install_dependencies
  backup_existing_dir
  clone_or_update_repo
  ensure_env_file
  ensure_permissions
  run_install
  edit_env_if_needed
  run_deploy
  write_report
  log "Bootstrap concluído"
  printf 'Relatorio: %s\n' "${REPORT_FILE}"
}

main "$@"
