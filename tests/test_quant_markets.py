from __future__ import annotations

from services.markets import build_market_intelligence, normalize_internal_markets, normalize_market_name, normalize_selection_name


def _game(with_odds: bool = True) -> dict:
    return {
        "game_id": "test-quant-001",
        "home": "Time Casa",
        "away": "Time Fora",
        "minute": 64,
        "home_pressure": 130,
        "away_pressure": 40,
        "home_shots_on": 6,
        "away_shots_on": 1,
        "markets": {
            "live_facts": {
                "shots_home": 15,
                "shots_away": 4,
                "corners_home": 8,
                "corners_away": 2,
                "yellow_home": 3,
                "yellow_away": 3,
                "dangerous_attacks_home": 58,
                "dangerous_attacks_away": 14,
            },
            "corners": {"over": {"line": "9.5", "odds": 2.02 if with_odds else None}},
            "cards": {"over": {"line": "5.5", "odds": 1.9 if with_odds else None}},
            "asian": {"home": {"line": "-0.5", "odds": 1.88 if with_odds else None}},
        },
    }


def test_market_name_normalization() -> None:
    assert normalize_market_name("Asian Handicap") == "asian_handicap"
    assert normalize_market_name("Corners 1st Half") == "corners_ht"
    assert normalize_market_name("Cards Over/Under") == "cards_total"
    assert normalize_selection_name("Time Casa", "Time Casa", "Time Fora") == "home"


def test_internal_normalizer_does_not_invent_odds() -> None:
    offers = normalize_internal_markets(_game(False))
    assert offers
    assert all(not item["is_confirmed"] for item in offers)


def test_market_intelligence_blocks_entries_without_confirmed_odds() -> None:
    payload = build_market_intelligence(_game(False))
    assert payload["confirmed_offers"] == 0
    assert all(item["action"] != "ENTRAR" for item in payload["recommendations"])


def test_market_intelligence_supports_confirmed_quant_markets() -> None:
    payload = build_market_intelligence(_game(True))
    markets = {item["market"] for item in payload["recommendations"]}
    assert {"Escanteios", "Cartões", "Asian Handicap"}.issubset(markets)
    assert payload["confirmed_offers"] >= 3
