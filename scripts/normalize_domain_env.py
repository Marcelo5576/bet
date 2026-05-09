from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse


def _normalize_domain(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return "apexgol.com.br"
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if not host:
        return "apexgol.com.br"
    return host.split(":", 1)[0]


def normalize_env_file(path: Path, domain: str, ip_hint: str = "2.24.217.214") -> dict[str, str]:
    host = _normalize_domain(domain)
    app_url = f"https://{host}"
    values = {
        "APP_URL": app_url,
        "WEBSITE_URL": app_url,
        "DASHBOARD_DOMAINS": f"http://{ip_hint},https://{host},https://www.{host}",
    }
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _ = line.split("=", 1)
        key = key.strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")

    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return values


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: normalize_domain_env.py <env-path> <domain> [ip_hint]", file=sys.stderr)
        return 1
    values = normalize_env_file(Path(argv[1]), argv[2], argv[3] if len(argv) > 3 else "2.24.217.214")
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
