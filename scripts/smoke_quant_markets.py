from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.markets import build_market_intelligence, normalize_market_name, normalize_selection_name


def _sample_game(with_odds: bool = True) -> dict:
    odd = 2.04 if with_odds else None
    return {
        "game_id": "smoke-quant-001",
        "league": "Smoke League",
        "home": "Apex Home",
        "away": "Apex Away",
        "minute": 62,
        "home_goals": 1,
        "away_goals": 0,
        "home_pressure": 126,
        "away_pressure": 42,
        "home_shots_on": 6,
        "away_shots_on": 1,
        "markets": {
            "live_facts": {
                "shots_home": 14,
                "shots_away": 4,
                "shots_on_home": 6,
                "shots_on_away": 1,
                "corners_home": 7,
                "corners_away": 2,
                "yellow_home": 3,
                "yellow_away": 3,
                "red_home": 0,
                "red_away": 0,
                "dangerous_attacks_home": 56,
                "dangerous_attacks_away": 16,
                "possession_home": 64,
                "possession_away": 36,
            },
            "corners": {
                "over": {"line": "9.5", "odds": odd},
                "under": {"line": "9.5", "odds": 1.78 if with_odds else None},
            },
            "cards": {
                "over": {"line": "5.5", "odds": 1.92 if with_odds else None},
                "under": {"line": "5.5", "odds": 1.88 if with_odds else None},
            },
            "asian": {
                "home": {"line": "-0.5", "odds": 1.91 if with_odds else None},
                "away": {"line": "+0.5", "odds": 1.95 if with_odds else None},
            },
        },
    }


def main() -> None:
    assert normalize_market_name("Asian Handicap") == "asian_handicap"
    assert normalize_market_name("Corners 1st Half") == "corners_ht"
    assert normalize_market_name("Cards Over/Under") == "cards_total"
    assert normalize_selection_name("Apex Home", "Apex Home", "Apex Away") == "home"

    live = build_market_intelligence(_sample_game(True))
    assert live["confirmed_offers"] >= 5
    groups = {item["market"] for item in live["recommendations"]}
    assert {"Escanteios", "Cartões", "Asian Handicap"}.issubset(groups)
    assert any(item["action"] == "ENTRAR" and item["confirmed"] for item in live["recommendations"])
    assert live["safety"]["entry_requires_confirmed_odd"] is True
    assert live["safety"]["no_mock_odds"] is True

    no_odds = build_market_intelligence(_sample_game(False))
    assert no_odds["confirmed_offers"] == 0
    assert all(item["action"] != "ENTRAR" for item in no_odds["recommendations"])
    assert no_odds["safety"]["blocked_without_odd"]

    print("quant-markets-smoke-ok")


if __name__ == "__main__":
    main()
