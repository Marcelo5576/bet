from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any
import unicodedata

from services.footballQuantAiSkill.config import load_research_skill_settings
from services.footballQuantAiSkill.feature_engineering.historical_feature_store import get_training_context
from services.footballQuantAiSkill.repository import FootballResearchRepository


_SERVICE_CACHE: dict[str, "HistoricalLiveContextService"] = {}
_SERVICE_LOCK = threading.Lock()


def get_historical_live_context_service() -> "HistoricalLiveContextService":
    db_file = load_research_skill_settings().db_file
    resolved = str(Path(db_file).resolve())
    with _SERVICE_LOCK:
        service = _SERVICE_CACHE.get(resolved)
        if service is None:
            service = HistoricalLiveContextService(resolved)
            _SERVICE_CACHE[resolved] = service
        return service


class HistoricalLiveContextService:
    def __init__(self, db_file: str, *, ttl_seconds: int = 180, max_context_matches: int = 2500):
        self.db_file = str(db_file)
        self.ttl_seconds = max(30, int(ttl_seconds))
        self.max_context_matches = max(250, int(max_context_matches))
        self.repository = FootballResearchRepository(self.db_file)
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

    def build_for_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        signal = dict(signal or {})
        game = signal.get("game") if isinstance(signal.get("game"), dict) else {}
        home = str(game.get("home") or signal.get("home_team") or "").strip()
        away = str(game.get("away") or signal.get("away_team") or "").strip()
        league = str(game.get("league") or game.get("division") or signal.get("league_name") or "").strip()
        market = str(signal.get("entry_market") or signal.get("market") or "").strip()
        selection = str(signal.get("entry_selection") or signal.get("selection") or signal.get("team") or "").strip()
        if not home or not away or not league:
            return {
                "available": False,
                "reason": "Dados insuficientes para comparar com a base historica.",
                "db_file": self.db_file,
            }

        cutoff = _resolve_cutoff(game)
        cache_key = "|".join(
            (
                cutoff,
                _clean_token(league),
                _clean_token(home),
                _clean_token(away),
                _clean_token(market),
                _clean_token(selection),
            )
        )
        cached = self._load_cached(cache_key)
        if cached is not None:
            return dict(cached)

        context = self._build_context(
            cutoff=cutoff,
            league=league,
            home=home,
            away=away,
            market=market,
            selection=selection,
        )
        self._store_cached(cache_key, context)
        return dict(context)

    def _build_context(
        self,
        *,
        cutoff: str,
        league: str,
        home: str,
        away: str,
        market: str,
        selection: str,
    ) -> dict[str, Any]:
        context = get_training_context(self.repository, cutoff, limit=self.max_context_matches)
        historical_matches = [dict(row) for row in context.get("matches") or []]
        if not historical_matches:
            return {
                "available": False,
                "reason": "Base historica vazia ou ainda nao importada.",
                "cutoff": cutoff,
                "db_file": self.db_file,
            }

        league_matches = [
            row
            for row in historical_matches
            if _same_text(row.get("league_name") or row.get("league"), league)
        ]
        home_matches = [row for row in historical_matches if _team_in_match(row, home)]
        away_matches = [row for row in historical_matches if _team_in_match(row, away)]

        home_stats = _team_context_from_matches(home_matches, home)
        away_stats = _team_context_from_matches(away_matches, away)
        league_stats = _league_context(league_matches)
        reliability = self._league_reliability(league)
        market_fit_score = _market_fit_score(
            market=market,
            selection=selection,
            home=home,
            away=away,
            home_stats=home_stats,
            away_stats=away_stats,
            league_stats=league_stats,
            league_reliability_score=reliability["score"],
        )
        coverage_score = min(
            1.0,
            max(
                0.0,
                (
                    (league_stats["trainable_matches"] / 80.0)
                    + (min(home_stats["sample_size"], away_stats["sample_size"]) / 15.0)
                )
                / 2.0,
            ),
        )
        historical_performance_score = max(
            0.0,
            min(
                1.0,
                (market_fit_score * 0.55)
                + (reliability["score"] * 0.30)
                + (coverage_score * 0.15),
            ),
        )

        positive_reasons: list[str] = []
        blocking_reasons: list[str] = []
        if reliability["score"] >= 0.72:
            positive_reasons.append("Base historica confiavel para esta liga.")
        elif reliability["classification"] == "Evitar":
            blocking_reasons.append("Liga marcada como evitar pela base historica.")
        elif reliability["classification"] == "Em observacao":
            blocking_reasons.append("Liga em observacao na base historica.")
        if market_fit_score >= 0.65:
            positive_reasons.append("Comparacao historica favorece o mercado atual.")
        elif market_fit_score <= 0.35 and league_stats["trainable_matches"] >= 20:
            blocking_reasons.append("Comparacao historica nao confirma o mercado atual.")
        if min(home_stats["sample_size"], away_stats["sample_size"]) < 3:
            blocking_reasons.append("Pouca amostra historica recente para os times.")

        summary = _comparison_summary(
            league=league,
            league_stats=league_stats,
            reliability=reliability,
            home=home,
            away=away,
            home_stats=home_stats,
            away_stats=away_stats,
            market_fit_score=market_fit_score,
        )

        return {
            "available": True,
            "cutoff": cutoff,
            "db_file": self.db_file,
            "league": league,
            "home_team": home,
            "away_team": away,
            "league_sample_size": league_stats["match_count"],
            "usable_training_matches": league_stats["trainable_matches"],
            "avg_data_quality": league_stats["avg_data_quality"],
            "league_avg_goals": round(league_stats["avg_goals"], 3),
            "league_over_2_5_rate": round(league_stats["over_2_5_rate"], 4),
            "league_btts_rate": round(league_stats["btts_rate"], 4),
            "home_recent_form_5": round(home_stats["form_5"], 3),
            "away_recent_form_5": round(away_stats["form_5"], 3),
            "home_goals_avg_5": round(home_stats["goals_for_avg_5"], 3),
            "away_goals_avg_5": round(away_stats["goals_for_avg_5"], 3),
            "home_conceded_avg_5": round(home_stats["goals_against_avg_5"], 3),
            "away_conceded_avg_5": round(away_stats["goals_against_avg_5"], 3),
            "home_strength": round(home_stats["strength"], 4),
            "away_strength": round(away_stats["strength"], 4),
            "team_sample_size": min(home_stats["sample_size"], away_stats["sample_size"]),
            "league_reliability_score": round(reliability["score"], 4),
            "league_reliability_raw": reliability["raw_score"],
            "league_classification": reliability["classification"],
            "league_reasons": reliability["reasons"],
            "market_fit_score": round(market_fit_score, 4),
            "market_fit_label": _fit_label(market_fit_score),
            "historical_performance_score": round(historical_performance_score, 4),
            "comparison_summary": summary,
            "positive_reasons": positive_reasons,
            "blocking_reasons": blocking_reasons,
        }

    def _league_reliability(self, league: str) -> dict[str, Any]:
        normalized = _clean_token(league)
        with self.repository.connect() as conn:
            rows = conn.execute(
                """
                SELECT league, season, match_count, trainable_count, odds_count, stats_count,
                       avg_data_quality, roi_simulated, drawdown, stability_score,
                       league_reliability_score, classification, reasons_json
                FROM league_reliability_scores
                ORDER BY season DESC, league_reliability_score DESC
                """
            ).fetchall()
        for row in rows:
            if _same_text(row["league"], normalized):
                raw_score = float(row["league_reliability_score"] or 0.0)
                return {
                    "score": max(0.0, min(1.0, raw_score / 100.0)),
                    "raw_score": raw_score,
                    "classification": str(row["classification"] or ""),
                    "reasons": _as_list(row["reasons_json"]),
                }
        return {
            "score": 0.45,
            "raw_score": 45.0,
            "classification": "Sem base",
            "reasons": ["Liga ainda sem consolidacao historica suficiente."],
        }

    def _load_cached(self, cache_key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._cache_lock:
            item = self._cache.get(cache_key)
            if not item:
                return None
            if now - item[0] > self.ttl_seconds:
                self._cache.pop(cache_key, None)
                return None
            return dict(item[1])

    def _store_cached(self, cache_key: str, payload: dict[str, Any]) -> None:
        with self._cache_lock:
            self._cache[cache_key] = (time.time(), dict(payload))


def _resolve_cutoff(game: dict[str, Any]) -> str:
    for key in ("kickoff_at", "match_date", "last_update_at", "updated_at"):
        value = str(game.get(key) or "").strip()
        if value:
            return value
    return datetime.now(timezone.utc).isoformat()


def _league_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "match_count": 0,
            "trainable_matches": 0,
            "avg_data_quality": 0.0,
            "avg_goals": 0.0,
            "over_2_5_rate": 0.0,
            "btts_rate": 0.0,
        }
    goals = []
    over_hits = 0
    btts_hits = 0
    trainable = 0
    quality_total = 0.0
    for row in rows:
        home_goals = _safe_int(row.get("home_goals"))
        away_goals = _safe_int(row.get("away_goals"))
        total_goals = home_goals + away_goals
        goals.append(total_goals)
        if total_goals >= 3:
            over_hits += 1
        if home_goals > 0 and away_goals > 0:
            btts_hits += 1
        quality = _safe_int(row.get("data_quality_score"))
        quality_total += quality
        if quality >= 70:
            trainable += 1
    size = len(rows)
    return {
        "match_count": size,
        "trainable_matches": trainable,
        "avg_data_quality": round(quality_total / max(1, size), 2),
        "avg_goals": round(sum(goals) / max(1, size), 3),
        "over_2_5_rate": over_hits / max(1, size),
        "btts_rate": btts_hits / max(1, size),
    }


def _team_context_from_matches(rows: list[dict[str, Any]], team: str) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    for row in rows:
        if _team_in_match(row, team):
            recent.append(row)
        if len(recent) >= 5:
            break
    if not recent:
        return {
            "sample_size": 0,
            "form_5": 0.0,
            "goals_for_avg_5": 0.0,
            "goals_against_avg_5": 0.0,
            "scoring_rate": 0.0,
            "strength": 0.0,
        }
    points: list[float] = []
    goals_for: list[float] = []
    goals_against: list[float] = []
    scoring_hits = 0
    for row in recent:
        gf, ga = _team_goals(row, team)
        goals_for.append(gf)
        goals_against.append(ga)
        points.append(3 if gf > ga else (1 if gf == ga else 0))
        if gf > 0:
            scoring_hits += 1
    form = sum(points) / max(1, len(points))
    gf_avg = sum(goals_for) / max(1, len(goals_for))
    ga_avg = sum(goals_against) / max(1, len(goals_against))
    strength = (form / 3.0 * 0.45) + (min(3.0, gf_avg) / 3.0 * 0.35) + (max(0.0, 3.0 - min(3.0, ga_avg)) / 3.0 * 0.20)
    return {
        "sample_size": len(recent),
        "form_5": round(form, 3),
        "goals_for_avg_5": round(gf_avg, 3),
        "goals_against_avg_5": round(ga_avg, 3),
        "scoring_rate": round(scoring_hits / max(1, len(recent)), 4),
        "strength": round(strength, 4),
    }


def _market_fit_score(
    *,
    market: str,
    selection: str,
    home: str,
    away: str,
    home_stats: dict[str, Any],
    away_stats: dict[str, Any],
    league_stats: dict[str, Any],
    league_reliability_score: float,
) -> float:
    market_key = _clean_token(market)
    selection_key = _clean_token(selection)
    generic = max(
        0.0,
        min(
            1.0,
            (league_reliability_score * 0.45)
            + (((home_stats["strength"] + away_stats["strength"]) / 2.0) * 0.35)
            + (min(home_stats["sample_size"], away_stats["sample_size"]) / 10.0 * 0.20),
        ),
    )
    if any(token in market_key for token in ("btts", "ambas", "marcam")):
        return max(
            0.0,
            min(
                1.0,
                (league_stats["btts_rate"] * 0.45)
                + (((home_stats["scoring_rate"] + away_stats["scoring_rate"]) / 2.0) * 0.35)
                + (league_reliability_score * 0.20),
            ),
        )
    if any(token in market_key for token in ("over", "under", "gols", "goals")) and "corners" not in market_key and "cards" not in market_key:
        attacking_total = min(1.0, (home_stats["goals_for_avg_5"] + away_stats["goals_for_avg_5"]) / 3.5)
        if "under" in market_key:
            return max(
                0.0,
                min(
                    1.0,
                    ((1.0 - league_stats["over_2_5_rate"]) * 0.45)
                    + ((1.0 - attacking_total) * 0.35)
                    + (league_reliability_score * 0.20),
                ),
            )
        return max(
            0.0,
            min(
                1.0,
                (league_stats["over_2_5_rate"] * 0.45)
                + (attacking_total * 0.35)
                + (league_reliability_score * 0.20),
            ),
        )
    if any(token in market_key for token in ("1x2", "resultado", "winner", "match winner")):
        delta = home_stats["strength"] - away_stats["strength"]
        if _same_text(selection_key, home) or selection_key == "home":
            return max(0.0, min(1.0, 0.50 + (delta * 0.50)))
        if _same_text(selection_key, away) or selection_key == "away":
            return max(0.0, min(1.0, 0.50 + ((-delta) * 0.50)))
        if selection_key == "draw":
            return max(0.0, min(1.0, 1.0 - abs(delta)))
    return generic


def _comparison_summary(
    *,
    league: str,
    league_stats: dict[str, Any],
    reliability: dict[str, Any],
    home: str,
    away: str,
    home_stats: dict[str, Any],
    away_stats: dict[str, Any],
    market_fit_score: float,
) -> str:
    return (
        f"Base historica: {league_stats['match_count']} jogos da liga {league}, "
        f"{league_stats['trainable_matches']} validos para treino, confiabilidade {int(round(reliability['score'] * 100))}/100. "
        f"{home} vem com forma {home_stats['form_5']:.2f} e {away} com forma {away_stats['form_5']:.2f}. "
        f"Aderencia historica do mercado: {int(round(market_fit_score * 100))}/100."
    )


def _team_goals(row: dict[str, Any], team: str) -> tuple[float, float]:
    if _same_text(row.get("home_team"), team):
        return float(row.get("home_goals") or 0), float(row.get("away_goals") or 0)
    return float(row.get("away_goals") or 0), float(row.get("home_goals") or 0)


def _team_in_match(row: dict[str, Any], team: str) -> bool:
    return _same_text(row.get("home_team"), team) or _same_text(row.get("away_team"), team)


def _same_text(left: Any, right: Any) -> bool:
    norm_left = _clean_token(left)
    norm_right = _clean_token(right)
    if not norm_left or not norm_right:
        return False
    if norm_left == norm_right:
        return True
    if len(norm_left) >= 5 and norm_left in norm_right:
        return True
    if len(norm_right) >= 5 and norm_right in norm_left:
        return True
    return False


def _clean_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _fit_label(score: float) -> str:
    if score >= 0.72:
        return "forte"
    if score >= 0.50:
        return "moderado"
    if score >= 0.35:
        return "fraco"
    return "contra"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            import json

            decoded = json.loads(value)
        except Exception:
            return [value.strip()]
        if isinstance(decoded, list):
            return [str(item) for item in decoded if str(item).strip()]
    return []
