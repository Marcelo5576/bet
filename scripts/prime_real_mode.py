from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings, load_settings  # noqa: E402
from src.intelligence.paper_trading import best_paper_entry, paper_opportunities  # noqa: E402
from src.intelligence.rules import ranked_signals  # noqa: E402
from src.main import (  # noqa: E402
    _simulate_learning_session,
    _watch_signal_from_game,
    build_provider,
    prepare_signal,
    scan_games,
)
from src.storage import StateStore  # noqa: E402


def _settings_warnings(settings: Settings) -> list[str]:
    warnings: list[str] = []
    if settings.test_mode:
        warnings.append("TEST_MODE=true (deve ficar false para modo real).")
    if not settings.telegram_bot_token:
        warnings.append("TELEGRAM_BOT_TOKEN ausente.")
    if not settings.api_football_key:
        warnings.append("API_FOOTBALL_KEY ausente; usando ESPN como fallback real.")
    if not settings.gemini_api_key:
        warnings.append("GEMINI_API_KEY ausente; sem refinamento textual da IA.")
    if not (settings.supabase_url and settings.supabase_service_role_key):
        warnings.append("Supabase nao configurado; memoria externa desligada.")
    return warnings


async def _run(seed_state: bool, simulate_now: bool) -> dict:
    settings = load_settings()
    store = StateStore(settings.state_file)
    state = store.load()
    provider = build_provider(settings)

    games, scan_scope = await scan_games(
        provider,
        state.scan_preference,
        block_esports=settings.block_esports,
    )

    base_signals = ranked_signals(
        games,
        settings.min_confidence,
        settings.bankroll,
        settings.unit_percent,
        settings.max_stake_units,
    )
    if base_signals:
        signals = [prepare_signal(signal, state, settings) for signal in base_signals[:12]]
    else:
        signals = [_watch_signal_from_game(game, state, settings) for game in games[:12]]

    if seed_state:
        store.set_last_games(games)
        store.set_candidates(signals)

    opportunities = paper_opportunities(signals)
    simulation = None
    if simulate_now and opportunities:
        now_local = datetime.now(ZoneInfo(settings.auto_simulation_timezone))
        session = _simulate_learning_session(
            opportunities,
            total_games=settings.auto_simulation_games,
            bankroll_units=settings.auto_simulation_bankroll,
            stake_percent=settings.auto_simulation_stake_percent,
            seed_key=f"manual|{now_local.date().isoformat()}|{scan_scope}",
        )
        session["scan_scope"] = scan_scope
        session["trigger"] = "manual_prime"
        session["source_games"] = len(games)
        store.add_simulation_session(session)
        if seed_state and games:
            refreshed_state = store.load()
            refreshed_ranked = ranked_signals(
                games,
                settings.min_confidence,
                settings.bankroll,
                settings.unit_percent,
                settings.max_stake_units,
            )
            if refreshed_ranked:
                store.set_candidates(
                    [prepare_signal(signal, refreshed_state, settings) for signal in refreshed_ranked[:12]]
                )
        simulation = {
            "total_games": session.get("total_games"),
            "greens": session.get("greens"),
            "reds": session.get("reds"),
            "profit_units": session.get("profit_units"),
            "roi": session.get("roi"),
        }

    refreshed = store.load()
    return {
        "provider": type(provider).__name__,
        "warnings": _settings_warnings(settings),
        "scan_scope": scan_scope,
        "live_games": len(games),
        "candidate_signals": len(signals),
        "paper_opportunities": len(opportunities),
        "best_paper_entry": best_paper_entry(signals),
        "seed_state": seed_state,
        "simulate_now": simulate_now,
        "simulation": simulation,
        "state_after": {
            "last_games": len(refreshed.last_games or []),
            "candidate_signals": len(refreshed.candidate_signals or []),
            "simulation_sessions": len(refreshed.simulation_sessions or []),
            "scan_preference": refreshed.scan_preference,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Liga o modo real do ApexGol no runtime atual, semeando jogos e sinais reais."
    )
    parser.add_argument(
        "--seed-state",
        action="store_true",
        help="Salva os jogos e candidatos reais no state.json.",
    )
    parser.add_argument(
        "--simulate-now",
        action="store_true",
        help="Roda uma simulacao imediata usando oportunidades do feed real atual.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime saida estruturada em JSON.",
    )
    args = parser.parse_args()

    report = asyncio.run(_run(args.seed_state, args.simulate_now))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=== ApexGol Real Mode Prime ===")
        print(f"provider: {report['provider']}")
        print(f"scan_scope: {report['scan_scope']}")
        print(f"live_games: {report['live_games']}")
        print(f"candidate_signals: {report['candidate_signals']}")
        print(f"paper_opportunities: {report['paper_opportunities']}")
        print(f"seed_state: {report['seed_state']}")
        print(f"simulate_now: {report['simulate_now']}")
        if report["warnings"]:
            print("warnings:")
            for item in report["warnings"]:
                print(f"  - {item}")
        if report["best_paper_entry"]:
            best = report["best_paper_entry"]
            print("best_paper_entry:")
            print(
                f"  - {best.get('match')} | {best.get('market')} | "
                f"{best.get('selection')} | odd {best.get('odds')}"
            )
        if report["simulation"]:
            print("simulation:")
            for key, value in report["simulation"].items():
                print(f"  - {key}: {value}")
        print("state_after:")
        for key, value in report["state_after"].items():
            print(f"  - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
