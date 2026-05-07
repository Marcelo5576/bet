#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
BACKUP_ROOT="${BACKUP_ROOT:-/root/backups/betsignal}"
REPORT_FILE="${PROJECT_DIR}/install_report.txt"
SCRIPT_PATH="$0"
STAMP="$(date +%Y%m%d-%H%M%S)"
AUTO_DEPLOY="${AUTO_DEPLOY:-true}"
SKIP_SYSTEM_PACKAGES="${SKIP_SYSTEM_PACKAGES:-false}"

if [[ -f "${SCRIPT_PATH}" ]]; then
  PACKAGE_DIR="${PACKAGE_DIR:-$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)}"
else
  PACKAGE_DIR="${PACKAGE_DIR:-$PWD}"
fi

required_package_items=(
  "docker-compose.yml"
  "Dockerfile"
  "requirements.txt"
  "src"
  "services"
  "scripts"
  "migrations"
  "Caddyfile"
  ".env.example"
  "deploy_apexgol.sh"
  "rollback_apexgol.sh"
  "diagnostics.sh"
  "server_bootstrap_apexgol.sh"
  "create_deploy_user.sh"
  "README_INSTALL.md"
  "CODEX_SERVER_ACCESS.md"
)

copy_items=(
  "assets"
  "migrations"
  "scripts"
  "services"
  "src"
  "tests"
  "docs"
  "Caddyfile"
  "docker-compose.yml"
  "Dockerfile"
  "requirements.txt"
  ".env.example"
  "README.md"
  "README_INSTALL.md"
  "CODEX_SERVER_ACCESS.md"
  "supabase_schema.sql"
  "install_apexgol.sh"
  "deploy_apexgol.sh"
  "rollback_apexgol.sh"
  "diagnostics.sh"
  "server_bootstrap_apexgol.sh"
  "create_deploy_user.sh"
)

INSTALL_STATUS="pending"
INSTALL_NOTES=()

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
    INSTALL_NOTES+=("Pacotes do sistema preservados por configuracao.")
    return
  fi

  export DEBIAN_FRONTEND=noninteractive
  log "Instalando dependências do servidor"
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
    INSTALL_NOTES+=("Docker existente reutilizado.")
  else
    apt-get install -y docker.io docker-compose-plugin
    systemctl enable --now docker
    INSTALL_NOTES+=("Docker instalado pelo instalador.")
  fi
}

validate_package() {
  log "Validando conteúdo do projeto"
  for item in "${required_package_items[@]}"; do
    [[ -e "${PACKAGE_DIR}/${item}" ]] || fail "Item obrigatório ausente: ${item}"
  done
}

paths_match() {
  local left right
  [[ -d "$1" && -d "$2" ]] || return 1
  left="$(cd "$1" && pwd)"
  right="$(cd "$2" && pwd)"
  [[ "${left}" == "${right}" ]]
}

prepare_project_dir() {
  local backup_dir old_env
  backup_dir="${BACKUP_ROOT}/install-backup-${STAMP}"
  old_env="${backup_dir}/.env"

  mkdir -p "${BACKUP_ROOT}"

  if [[ -d "${PROJECT_DIR}" ]] && ! paths_match "${PACKAGE_DIR}" "${PROJECT_DIR}"; then
    log "Instalação anterior encontrada. Movendo para ${backup_dir}"
    mv "${PROJECT_DIR}" "${backup_dir}"
  fi

  if paths_match "${PACKAGE_DIR}" "${PROJECT_DIR}"; then
    log "Projeto já está em ${PROJECT_DIR}. Instalação será feita in-place."
    mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/backups"
    if [[ -f "${old_env}" && ! -f "${PROJECT_DIR}/.env" ]]; then
      cp "${old_env}" "${PROJECT_DIR}/.env"
    fi
    return
  fi

  log "Copiando projeto para ${PROJECT_DIR}"
  mkdir -p "${PROJECT_DIR}"
  for item in "${copy_items[@]}"; do
    if [[ -e "${PACKAGE_DIR}/${item}" ]]; then
      cp -a "${PACKAGE_DIR}/${item}" "${PROJECT_DIR}/"
    fi
  done
  mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/backups"

  if [[ -f "${old_env}" && ! -f "${PROJECT_DIR}/.env" ]]; then
    log "Restaurando .env da instalação anterior"
    cp "${old_env}" "${PROJECT_DIR}/.env"
  fi
}

ensure_env_file() {
  if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    log "Criando .env a partir de .env.example"
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
  else
    log ".env existente preservado"
  fi
}

ensure_script_permissions() {
  chmod +x \
    "${PROJECT_DIR}/install_apexgol.sh" \
    "${PROJECT_DIR}/deploy_apexgol.sh" \
    "${PROJECT_DIR}/rollback_apexgol.sh" \
    "${PROJECT_DIR}/diagnostics.sh" \
    "${PROJECT_DIR}/server_bootstrap_apexgol.sh" \
    "${PROJECT_DIR}/create_deploy_user.sh"
}

list_missing_env() {
  python3 - "${PROJECT_DIR}/.env" <<'PY'
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

maybe_edit_env() {
  local missing
  missing="$(list_missing_env || true)"
  if [[ -z "${missing}" ]]; then
    return
  fi

  printf 'Preencha as variáveis obrigatórias no arquivo %s/.env:\n%s\n' "${PROJECT_DIR}" "${missing}"
  INSTALL_STATUS="waiting_env"
  INSTALL_NOTES+=("Variáveis obrigatórias pendentes no .env.")

  if command -v nano >/dev/null 2>&1; then
    read -r -p "Abrir o .env agora com nano? [Y/n] " answer
    if [[ -z "${answer}" || "${answer}" =~ ^[Yy]$ ]]; then
      nano "${PROJECT_DIR}/.env"
    fi
  fi

  missing="$(list_missing_env || true)"
  if [[ -n "${missing}" ]]; then
    printf 'Ainda faltam variáveis obrigatórias:\n%s\n' "${missing}"
    printf 'Edite %s/.env e depois rode ./deploy_apexgol.sh\n' "${PROJECT_DIR}"
    return
  fi
}

run_deploy_if_ready() {
  local missing
  missing="$(list_missing_env || true)"
  if [[ -n "${missing}" ]]; then
    return
  fi
  if [[ "${AUTO_DEPLOY}" != "true" ]]; then
    INSTALL_STATUS="ready_to_deploy"
    INSTALL_NOTES+=("Ambiente pronto. Deploy automatico desativado para este fluxo.")
    return
  fi
  log "Variáveis obrigatórias preenchidas. Executando deploy inicial."
  cd "${PROJECT_DIR}"
  ./deploy_apexgol.sh
  INSTALL_STATUS="deployed"
  INSTALL_NOTES+=("Deploy inicial executado com sucesso.")
}

write_report() {
  mkdir -p "${PROJECT_DIR}"
  {
    printf 'ApexGol AI - Install Report\n'
    printf 'Timestamp: %s\n' "$(date -Is)"
    printf 'Status: %s\n' "${INSTALL_STATUS}"
    printf 'Project Dir: %s\n' "${PROJECT_DIR}"
    printf 'Package Dir: %s\n' "${PACKAGE_DIR}"
    printf 'Backup Root: %s\n' "${BACKUP_ROOT}"
    printf 'Env File: %s/.env\n' "${PROJECT_DIR}"
    if [[ -d "${PROJECT_DIR}/.git" ]]; then
      printf 'Git Repo: sim\n'
    else
      printf 'Git Repo: nao\n'
    fi
    printf 'Notes:\n'
    if [[ "${#INSTALL_NOTES[@]}" -eq 0 ]]; then
      printf '- sem observacoes\n'
    else
      for note in "${INSTALL_NOTES[@]}"; do
        printf -- '- %s\n' "${note}"
      done
    fi
    printf 'Next Step: cd %s && ./deploy_apexgol.sh\n' "${PROJECT_DIR}"
  } >"${REPORT_FILE}"
}

show_urls() {
  python3 - "${PROJECT_DIR}/.env" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
env = {}
for line in path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value.strip()

app_url = env.get("APP_URL") or env.get("WEBSITE_URL") or "https://novo.tickpost.com.br"
print(f"Landing: {app_url}/")
print(f"Login: {app_url}/login")
print(f"Dashboard: {app_url}/dashboard")
print(f"Cerebro IA: {app_url}/cerebro-ia")
PY
}

main() {
  require_root
  validate_os
  validate_package
  install_dependencies
  prepare_project_dir
  ensure_env_file
  ensure_script_permissions
  maybe_edit_env
  run_deploy_if_ready
  write_report
  log "Instalação concluída"
  printf 'Relatorio: %s\n' "${REPORT_FILE}"
  show_urls
}

main "$@"
