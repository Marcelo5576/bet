from __future__ import annotations

import getpass
import posixpath
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for extra_dir in (".vendor", ".pylib", ".deps"):
    candidate = ROOT / extra_dir
    if candidate.exists():
        sys.path.insert(0, str(candidate))

import paramiko
REMOTE_ROOT = "/opt/betsignal-cloud"
DEFAULT_HOST = "2.24.217.214"
DEFAULT_USER = "root"
DEFAULT_PORT = 22

SYNC_ITEMS = [
    ".dockerignore",
    ".env.example",
    "Caddyfile",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "README.md",
    "REBUILD_APEXGOL_BR.md",
    "TELEGRAM_CONNECT_GUIDE_BR.md",
    "supabase_schema.sql",
    "assets",
    "scripts",
    "src",
]

EXCLUDED_PARTS = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
}

EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
}


def _remote(path: str) -> str:
    return posixpath.join(REMOTE_ROOT, path.replace("\\", "/"))


def _iter_files(item: str) -> list[tuple[Path, str]]:
    local_path = ROOT / item
    if not local_path.exists():
        return []
    if local_path.is_file():
        return [(local_path, item.replace("\\", "/"))]
    files: list[tuple[Path, str]] = []
    for path in local_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        files.append((path, rel))
    return files


def _check_port(host: str, port: int) -> None:
    sock = socket.socket()
    sock.settimeout(8)
    try:
        sock.connect((host, port))
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            f"SSH indisponivel em {host}:{port}. Erro: {exc}. "
            "Ajuste o firewall/regra de rede antes de publicar."
        )
    finally:
        sock.close()


def _run(ssh: paramiko.SSHClient, command: str, timeout: int = 120) -> str:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if exit_code != 0:
        raise RuntimeError(f"Comando falhou ({exit_code}): {command}\n{out}\n{err}")
    return out


def _mkdirs(sftp: paramiko.SFTPClient, remote_path: str) -> None:
    parts = remote_path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


def main() -> int:
    host = DEFAULT_HOST
    user = DEFAULT_USER
    port = DEFAULT_PORT
    password = getpass.getpass(f"Senha SSH para {user}@{host}: ")

    _check_port(host, port)

    files: list[tuple[Path, str]] = []
    for item in SYNC_ITEMS:
        files.extend(_iter_files(item))
    if not files:
        raise SystemExit("Nenhum arquivo para publicar.")

    print(f"Preparando publicacao para {host}:{port} -> {REMOTE_ROOT}")
    print(f"Arquivos selecionados: {len(files)}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password, timeout=20)

    try:
        stamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
        backup_dir = f"/root/backups/betsignal/{stamp}"
        print(f"Criando backup remoto em {backup_dir}")
        _run(
            ssh,
            " && ".join(
                [
                    f"mkdir -p {backup_dir}",
                    f"cp -a {REMOTE_ROOT}/src {backup_dir}/src",
                    f"cp -a {REMOTE_ROOT}/assets {backup_dir}/assets",
                    f"cp -a {REMOTE_ROOT}/requirements.txt {backup_dir}/requirements.txt",
                    f"cp -a {REMOTE_ROOT}/docker-compose.yml {backup_dir}/docker-compose.yml",
                    f"cp -a {REMOTE_ROOT}/Caddyfile {backup_dir}/Caddyfile",
                    f"cp -a {REMOTE_ROOT}/Dockerfile {backup_dir}/Dockerfile",
                ]
            ),
            timeout=240,
        )

        sftp = ssh.open_sftp()
        try:
            for local_path, rel_path in files:
                remote_path = _remote(rel_path)
                remote_dir = posixpath.dirname(remote_path)
                _mkdirs(sftp, remote_dir)
                print(f"Upload {rel_path}")
                sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()

        print("Rebuild dos containers...")
        print(
            _run(
                ssh,
                f"cd {REMOTE_ROOT} && docker compose build --no-cache dashboard betsignal",
                timeout=3600,
            )
        )
        print("Recriando containers...")
        print(
            _run(
                ssh,
                f"cd {REMOTE_ROOT} && docker compose up -d --force-recreate dashboard betsignal caddy",
                timeout=900,
            )
        )
        print("Validando servicos...")
        print(_run(ssh, f"cd {REMOTE_ROOT} && docker compose ps", timeout=120))
        print(_run(ssh, f"cd {REMOTE_ROOT} && docker compose logs --tail=80 dashboard betsignal caddy", timeout=240))
        print("Publicacao concluida.")
    finally:
        ssh.close()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Publicacao cancelada pelo usuario.")
