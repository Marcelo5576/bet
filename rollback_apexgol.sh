#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
BACKUP_ROOT="${BACKUP_ROOT:-${HOME}/backups/betsignal}"
STAMP="$(date +%Y%m%d-%H%M%S)"

log() {
  printf '==> %s\n' "$1"
}

fail() {
  printf 'ERRO: %s\n' "$1" >&2
  exit 1
}

collect_backups() {
  mapfile -t BACKUPS < <(find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d | sort)
  [[ "${#BACKUPS[@]}" -gt 0 ]] || fail "Nenhum backup encontrado em ${BACKUP_ROOT}"
}

choose_backup() {
  log "Backups disponíveis"
  local i=1
  for item in "${BACKUPS[@]}"; do
    printf '  [%s] %s\n' "${i}" "${item}"
    i=$((i + 1))
  done
  read -r -p "Escolha o número do backup para restaurar: " choice
  [[ "${choice}" =~ ^[0-9]+$ ]] || fail "Escolha inválida."
  [[ "${choice}" -ge 1 && "${choice}" -le "${#BACKUPS[@]}" ]] || fail "Escolha fora da faixa."
  SELECTED_BACKUP="${BACKUPS[$((choice - 1))]}"
}

backup_current_install() {
  if [[ -d "${PROJECT_DIR}" ]]; then
    mkdir -p "${BACKUP_ROOT}"
    local rollback_backup="${BACKUP_ROOT}/pre-rollback-${STAMP}"
    log "Salvando instalação atual em ${rollback_backup}"
    cp -a "${PROJECT_DIR}" "${rollback_backup}"
  fi
}

restore_backup() {
  log "Restaurando ${SELECTED_BACKUP} para ${PROJECT_DIR}"
  if [[ -d "${PROJECT_DIR}" ]]; then
    docker compose -f "${PROJECT_DIR}/docker-compose.yml" down || true
    rm -rf "${PROJECT_DIR}"
  fi
  mkdir -p "${PROJECT_DIR}"
  cp -a "${SELECTED_BACKUP}/." "${PROJECT_DIR}/"
}

rebuild_and_validate() {
  cd "${PROJECT_DIR}"
  docker compose build
  docker compose up -d --force-recreate
  docker compose exec -T dashboard python -m compileall -q src services scripts
  if [[ -f scripts/smoke_install.py ]]; then
    docker compose exec -T dashboard python scripts/smoke_install.py
  fi
}

show_status() {
  log "Rollback concluído"
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps
}

main() {
  collect_backups
  choose_backup
  backup_current_install
  restore_backup
  rebuild_and_validate
  show_status
}

main "$@"
