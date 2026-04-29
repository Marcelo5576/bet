from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.intelligence.learning import summarize_history_with_simulation  # noqa: E402


def _file_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _git_head() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        short = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return {"branch": branch, "commit": commit, "short_commit": short}
    except Exception:
        return {"branch": None, "commit": None, "short_commit": None}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_load_error": str(exc)}


def _learning_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    candidates = state.get("candidate_signals") or []
    active_signal = state.get("active_signal") or {}
    for source in [active_signal] + list(candidates):
        if not isinstance(source, dict):
            continue
        learning = source.get("learning_context")
        if not isinstance(learning, dict):
            continue
        overall = learning.get("overall") or {}
        fast = learning.get("fast_learning") or {}
        return {
            "sample_size": learning.get("sample_size"),
            "real_sample_size": learning.get("real_sample_size"),
            "simulation_sample_size": learning.get("simulation_sample_size"),
            "brier_score": learning.get("brier_score"),
            "profit_units": learning.get("profit_units"),
            "roi_units": learning.get("roi_units"),
            "wins": overall.get("wins"),
            "losses": overall.get("losses"),
            "hit_rate": overall.get("hit_rate"),
            "fast_mode": fast.get("mode"),
            "momentum_score": fast.get("momentum_score"),
        }
    return {}


def _effective_learning(state: dict[str, Any]) -> dict[str, Any]:
    learning = summarize_history_with_simulation(
        state.get("history") or [],
        state.get("simulation_sessions") or [],
        simulation_weight=0.35,
        max_simulation_rows=240,
    )
    overall = learning.get("overall") or {}
    fast = learning.get("fast_learning") or {}
    return {
        "sample_size": learning.get("sample_size"),
        "real_sample_size": learning.get("real_sample_size"),
        "simulation_sample_size": learning.get("simulation_sample_size"),
        "brier_score": learning.get("brier_score"),
        "profit_units": learning.get("profit_units"),
        "roi_units": learning.get("roi_units"),
        "wins": overall.get("wins"),
        "losses": overall.get("losses"),
        "hit_rate": overall.get("hit_rate"),
        "fast_mode": fast.get("mode"),
        "momentum_score": fast.get("momentum_score"),
    }


def _state_summary(state: dict[str, Any]) -> dict[str, Any]:
    history = state.get("history") or []
    outcomes = Counter(
        str(item.get("outcome") or "unknown")
        for item in history
        if isinstance(item, dict)
    )
    simulations = state.get("simulation_sessions") or []
    latest_sim = simulations[0] if simulations and isinstance(simulations[0], dict) else {}
    return {
        "active_game_id": state.get("active_game_id"),
        "active_signal": bool(state.get("active_signal")),
        "scan_preference": state.get("scan_preference"),
        "chat_ids": len(state.get("chat_ids") or []),
        "history_total": len(history),
        "history_outcomes": dict(outcomes),
        "candidate_signals": len(state.get("candidate_signals") or []),
        "last_games": len(state.get("last_games") or []),
        "simulation_sessions": len(simulations),
        "last_auto_simulation_date": state.get("last_auto_simulation_date"),
        "last_auto_simulation_at": state.get("last_auto_simulation_at"),
        "latest_simulation": {
            "total_games": latest_sim.get("total_games"),
            "greens": latest_sim.get("greens"),
            "reds": latest_sim.get("reds"),
            "profit_units": latest_sim.get("profit_units"),
            "created_at": latest_sim.get("created_at"),
        } if latest_sim else {},
        "learning_snapshot": _learning_snapshot(state),
        "effective_learning": _effective_learning(state),
    }


def _portal_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    con = sqlite3.connect(path.as_posix())
    con.row_factory = sqlite3.Row
    try:
        users = [dict(row) for row in con.execute("select * from users order by id")]
        table_counts = {}
        for table in ["users", "user_preferences", "payment_logs", "support_logs", "pricing_config"]:
            table_counts[table] = con.execute(f"select count(*) from {table}").fetchone()[0]
        return {
            "exists": True,
            "table_counts": table_counts,
            "admins": [
                {
                    "id": user["id"],
                    "email": user["email"],
                    "name": user["name"],
                    "status": user["status"],
                }
                for user in users
                if int(user["is_admin"] or 0) == 1
            ],
            "users": [
                {
                    "id": user["id"],
                    "email": user["email"],
                    "name": user["name"],
                    "plan": user["plan"],
                    "status": user["status"],
                }
                for user in users
            ],
        }
    finally:
        con.close()


def _config_summary() -> dict[str, Any]:
    settings = load_settings()
    return {
        "product_name": settings.product_name,
        "website_url": settings.website_url,
        "test_mode": settings.test_mode,
        "scan_interval_seconds": settings.scan_interval_seconds,
        "idle_scan_interval_seconds": settings.idle_scan_interval_seconds,
        "active_scan_interval_seconds": settings.active_scan_interval_seconds,
        "gemini_model": settings.gemini_model,
        "api_football_enabled": bool(settings.api_football_key),
        "gemini_enabled": bool(settings.gemini_api_key),
        "telegram_enabled": bool(settings.telegram_bot_token),
        "supabase_enabled": bool(settings.supabase_url and settings.supabase_service_role_key),
        "smtp_enabled": bool(settings.smtp_host and settings.smtp_from),
        "state_file": settings.state_file,
        "portal_db_file": settings.portal_db_file,
        "admin_email": settings.admin_email,
    }


def build_report() -> dict[str, Any]:
    state_path = ROOT / "data" / "state.json"
    portal_path = ROOT / "data" / "portal.db"
    state = _load_state(state_path)
    return {
        "project_root": str(ROOT),
        "git": _git_head(),
        "files": {
            "state_json": _file_meta(state_path),
            "portal_db": _file_meta(portal_path),
        },
        "config": _config_summary(),
        "state": _state_summary(state),
        "portal": _portal_summary(portal_path),
    }


def _print_text(report: dict[str, Any]) -> None:
    print("=== ApexGol Runtime Audit ===")
    print(f"root: {report['project_root']}")
    print(
        "git: "
        f"{report['git'].get('branch') or '-'} "
        f"{report['git'].get('short_commit') or '-'}"
    )
    print()
    print("Config:")
    for key, value in report["config"].items():
        print(f"  - {key}: {value}")
    print()
    print("Files:")
    for key, value in report["files"].items():
        print(f"  - {key}: {value}")
    print()
    print("State:")
    for key, value in report["state"].items():
        print(f"  - {key}: {value}")
    print()
    print("Portal:")
    print(f"  - exists: {report['portal'].get('exists')}")
    if report["portal"].get("exists"):
        print(f"  - table_counts: {report['portal'].get('table_counts')}")
        print(f"  - admins: {report['portal'].get('admins')}")
        print(f"  - users_total: {len(report['portal'].get('users') or [])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audita o estado atual do ApexGol.")
    parser.add_argument("--json", action="store_true", help="Imprime o relatorio em JSON.")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
