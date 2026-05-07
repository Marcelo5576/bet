#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
DEPLOY_HOME="${DEPLOY_HOME:-/home/${DEPLOY_USER}}"
PROJECT_DIR="${PROJECT_DIR:-/opt/betsignal-cloud}"
PUBLIC_KEY="${PUBLIC_KEY:-}"
PUBLIC_KEY_FILE="${PUBLIC_KEY_FILE:-}"
ASSIGN_PROJECT="${ASSIGN_PROJECT:-true}"
SUDOERS_FILE="/etc/sudoers.d/apexgol-${DEPLOY_USER}"

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

resolve_public_key() {
  if [[ -n "${PUBLIC_KEY}" ]]; then
    return
  fi

  if [[ -n "${PUBLIC_KEY_FILE}" && -f "${PUBLIC_KEY_FILE}" ]]; then
    PUBLIC_KEY="$(cat "${PUBLIC_KEY_FILE}")"
    return
  fi

  read -r -p "Cole a chave publica SSH do usuario deploy: " PUBLIC_KEY
  [[ -n "${PUBLIC_KEY}" ]] || fail "Chave publica não informada."
}

create_user_if_needed() {
  if id "${DEPLOY_USER}" >/dev/null 2>&1; then
    log "Usuario ${DEPLOY_USER} já existe"
  else
    log "Criando usuario ${DEPLOY_USER}"
    useradd -m -s /bin/bash "${DEPLOY_USER}"
  fi

  getent group docker >/dev/null 2>&1 || groupadd docker
  usermod -aG docker "${DEPLOY_USER}"
}

install_ssh_key() {
  log "Instalando chave publica em ${DEPLOY_HOME}/.ssh/authorized_keys"
  install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_HOME}/.ssh"
  touch "${DEPLOY_HOME}/.ssh/authorized_keys"
  chmod 600 "${DEPLOY_HOME}/.ssh/authorized_keys"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${DEPLOY_HOME}/.ssh/authorized_keys"

  if ! grep -Fq "${PUBLIC_KEY}" "${DEPLOY_HOME}/.ssh/authorized_keys"; then
    printf '%s\n' "${PUBLIC_KEY}" >>"${DEPLOY_HOME}/.ssh/authorized_keys"
  fi
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${DEPLOY_HOME}/.ssh/authorized_keys"
}

configure_sudo() {
  log "Configurando sudo restrito em ${SUDOERS_FILE}"
  cat >"${SUDOERS_FILE}" <<EOF
${DEPLOY_USER} ALL=(root) NOPASSWD: /usr/bin/docker, /usr/bin/systemctl reload caddy, /usr/bin/systemctl status caddy
EOF
  chmod 440 "${SUDOERS_FILE}"
}

assign_project_permissions() {
  if [[ "${ASSIGN_PROJECT}" != "true" ]]; then
    return
  fi
  if [[ -d "${PROJECT_DIR}" ]]; then
    log "Entregando posse de ${PROJECT_DIR} para ${DEPLOY_USER}"
    chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${PROJECT_DIR}"
  fi
}

show_result() {
  printf 'Usuario deploy pronto.\n'
  printf 'Login SSH: %s@SEU_SERVIDOR\n' "${DEPLOY_USER}"
  printf 'Projeto: %s\n' "${PROJECT_DIR}"
  printf 'Sudo restrito: docker e systemctl caddy\n'
}

main() {
  require_root
  resolve_public_key
  create_user_if_needed
  install_ssh_key
  configure_sudo
  assign_project_permissions
  show_result
}

main "$@"
