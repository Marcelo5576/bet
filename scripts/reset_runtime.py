from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings  # noqa: E402
from src.portal import PortalStore  # noqa: E402
from src.storage import BotState  # noqa: E402


def _backup_data() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "backups" / "runtime" / stamp
    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    data_dir = ROOT / "data"
    if data_dir.exists():
        shutil.copytree(data_dir, backup_dir / "data")
    return backup_dir


def _load_raw_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_clean_state(
    path: Path,
    *,
    preserve_scan_preference: bool,
    preserve_chat_ids: bool,
    preserve_simulations: bool,
) -> None:
    current = _load_raw_state(path)
    state = BotState(
        active_game_id=None,
        active_signal=None,
        last_scan_at=None,
        chat_ids=list(current.get("chat_ids") or []) if preserve_chat_ids else [],
        history=[],
        candidate_signals=[],
        last_games=[],
        simulation_sessions=list(current.get("simulation_sessions") or []) if preserve_simulations else [],
        last_auto_simulation_date=None,
        last_auto_simulation_at=None,
        scan_preference=(
            str(current.get("scan_preference") or "brazil_first")
            if preserve_scan_preference
            else "brazil_first"
        ),
        scan_requested_at=None,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _rebuild_portal_db(path: Path) -> None:
    if path.exists():
        path.unlink()
    settings = load_settings()
    store = PortalStore(path.as_posix())
    store.ensure_admin(settings.admin_email, settings.admin_name, settings.admin_password)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reseta o estado operacional do ApexGol com backup automatico."
    )
    parser.add_argument(
        "--wipe-portal-db",
        action="store_true",
        help="Tambem recria o portal.db, preservando apenas o admin definido no .env.",
    )
    parser.add_argument(
        "--drop-chat-ids",
        action="store_true",
        help="Nao preserva chat_ids do Telegram no novo state.json.",
    )
    parser.add_argument(
        "--drop-scan-preference",
        action="store_true",
        help="Nao preserva a preferencia atual do scanner.",
    )
    parser.add_argument(
        "--preserve-simulations",
        action="store_true",
        help="Mantem as simulation_sessions no novo state.json.",
    )
    args = parser.parse_args()

    state_path = ROOT / "data" / "state.json"
    portal_path = ROOT / "data" / "portal.db"

    backup_dir = _backup_data()
    _write_clean_state(
        state_path,
        preserve_scan_preference=not args.drop_scan_preference,
        preserve_chat_ids=not args.drop_chat_ids,
        preserve_simulations=args.preserve_simulations,
    )

    if args.wipe_portal_db:
        _rebuild_portal_db(portal_path)

    print("Backup criado em:", backup_dir)
    print("state.json resetado em:", state_path)
    if args.wipe_portal_db:
        print("portal.db recriado com admin do .env em:", portal_path)
    else:
        print("portal.db preservado em:", portal_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
