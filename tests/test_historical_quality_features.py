from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from services.footballQuantAiSkill.feature_engineering.historical_feature_store import (
    HistoricalFeatureStore,
    calculate_data_quality,
    get_training_context,
)
from services.footballQuantAiSkill.repository import FootballResearchRepository
from services.footballQuantAiSkill.schemas import NormalizedMatch


class HistoricalQualityFeatureTests(unittest.TestCase):
    def test_quality_score_requires_real_odds_for_training_grade(self):
        match = {
            "status": "FT",
            "home_goals": 2,
            "away_goals": 1,
            "league": "Serie A",
            "home_team": "Casa",
            "away_team": "Fora",
        }
        quality_without_odds = calculate_data_quality(
            match,
            odds=[],
            stats={"shots_home": 10},
            duplicate_count=1,
        )
        quality_with_mock_odds = calculate_data_quality(
            match,
            odds=[{"home_odd": 1.8, "source": "Mock Local", "is_real": 0}],
            stats={"shots_home": 10},
            duplicate_count=1,
        )
        quality_with_real_odds = calculate_data_quality(
            match,
            odds=[{"home_odd": 1.8, "source": "API-Football odds consensus", "is_real": 1}],
            stats={"shots_home": 10},
            duplicate_count=1,
        )
        self.assertEqual(quality_without_odds, 75)
        self.assertEqual(quality_with_mock_odds, 75)
        self.assertEqual(quality_with_real_odds, 100)

    def test_get_training_context_excludes_target_and_future_matches(self):
        with TemporaryDirectory() as tmp:
            repo = FootballResearchRepository(str(Path(tmp) / "research.db"))
            repo.import_normalized_matches(
                [
                    _match("100", "2024-01-01T12:00:00+00:00", 2, 0),
                    _match("101", "2024-02-01T12:00:00+00:00", 1, 1),
                    _match("102", "2024-03-01T12:00:00+00:00", 0, 3),
                ],
                source_name="API-Football",
            )
            store = HistoricalFeatureStore(repo)
            store.rebuild()
            context = get_training_context(repo, "2024-02-01T12:00:00+00:00", limit=10)
            ids = [row["external_id"] for row in context["matches"]]
            self.assertEqual(ids, ["100"])
            self.assertNotIn("101", ids)
            self.assertNotIn("102", ids)

    def test_feature_store_uses_only_previous_team_history(self):
        with TemporaryDirectory() as tmp:
            repo = FootballResearchRepository(str(Path(tmp) / "research.db"))
            repo.import_normalized_matches(
                [
                    _match("200", "2024-01-01T12:00:00+00:00", 2, 0),
                    _match("201", "2024-02-01T12:00:00+00:00", 1, 0),
                ],
                source_name="API-Football",
            )
            store = HistoricalFeatureStore(repo)
            store.rebuild()
            with repo.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT m.external_id, f.home_goals_avg_5, f.context_match_count
                    FROM historical_features f
                    JOIN historical_matches m ON m.id = f.match_id
                    ORDER BY m.match_date ASC
                    """
                ).fetchall()
            self.assertEqual(rows[0]["external_id"], "200")
            self.assertEqual(rows[0]["context_match_count"], 0)
            self.assertEqual(rows[1]["external_id"], "201")
            self.assertEqual(rows[1]["home_goals_avg_5"], 2.0)


def _match(external_id: str, date: str, home_goals: int, away_goals: int) -> NormalizedMatch:
    return NormalizedMatch(
        external_id=external_id,
        league="Teste League",
        country="BR",
        season=2024,
        match_date=datetime.fromisoformat(date).astimezone(timezone.utc),
        home_team="Time A",
        away_team="Time B",
        status="FT",
        home_goals=home_goals,
        away_goals=away_goals,
        source="API-Football",
        stats={"shots_home": 10, "shots_away": 5},
        odds=[
            {
                "market": "match_winner",
                "home_odd": 1.8,
                "draw_odd": 3.2,
                "away_odd": 4.5,
                "source": "API-Football odds consensus",
                "bookmaker": "market_average",
            }
        ],
        raw_payload={"fixture": {"id": external_id}},
    )


if __name__ == "__main__":
    unittest.main()
