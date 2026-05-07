from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from statistics import pstdev
from typing import Any

from ..repository import FootballResearchRepository


FEATURE_SET_VERSION = "v1_no_leakage"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoricalFeatureStore:
    def __init__(self, repository: FootballResearchRepository):
        self.repository = repository

    def rebuild(self, *, feature_set_version: str = FEATURE_SET_VERSION, limit: int | None = None) -> dict[str, Any]:
        matches = self._load_matches(limit=limit)
        splits = _temporal_splits(matches)
        odds_by_match = self._load_odds_by_match()
        stats_by_match = self._load_stats_by_match()
        duplicate_counts = _duplicate_counts(matches)
        team_history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        features_written = 0
        quality_rows: list[tuple[int, int, int, str, str, str, str, str]] = []
        feature_rows: list[dict[str, Any]] = []

        for match in matches:
            match_id = int(match["id"])
            split = splits.get(match_id, "train")
            odds = odds_by_match.get(match_id, [])
            stats = stats_by_match.get(match_id)
            duplicate_key = _duplicate_key(match)
            quality = calculate_data_quality(match, odds=odds, stats=stats, duplicate_count=duplicate_counts.get(duplicate_key, 1))
            normalized_payload = _normalized_payload(match, data_quality_score=quality, temporal_split=split)
            source_provider = str(match.get("source_provider") or match.get("source") or "unknown")
            quality_rows.append(
                (
                    quality,
                    1 if quality >= 70 else 0,
                    match_id,
                    source_provider,
                    str(match.get("external_fixture_id") or match.get("external_id") or ""),
                    str(match.get("league_name") or match.get("league") or ""),
                    duplicate_key,
                    split,
                )
            )

            home_context = _team_context_from_history(
                team_history[(str(match["league"]), str(match["home_team"]))],
                str(match["home_team"]),
            )
            away_context = _team_context_from_history(
                team_history[(str(match["league"]), str(match["away_team"]))],
                str(match["away_team"]),
            )
            implied_probability = _market_implied_probability(odds)
            feature_rows.append(
                {
                    "match_id": match_id,
                    "feature_set_version": feature_set_version,
                    "temporal_split": split,
                    "home_recent_form_5": home_context["form_5"],
                    "away_recent_form_5": away_context["form_5"],
                    "home_goals_avg_5": home_context["goals_for_avg_5"],
                    "away_goals_avg_5": away_context["goals_for_avg_5"],
                    "home_conceded_avg_5": home_context["goals_against_avg_5"],
                    "away_conceded_avg_5": away_context["goals_against_avg_5"],
                    "home_xg_avg_5": home_context["xg_avg_5"],
                    "away_xg_avg_5": away_context["xg_avg_5"],
                    "home_strength": home_context["strength"],
                    "away_strength": away_context["strength"],
                    "market_implied_probability": implied_probability,
                    "closing_line_value": None,
                    "data_quality_score": quality,
                    "usable_for_training": 1 if quality >= 70 else 0,
                    "context_match_count": min(home_context["sample_size"], away_context["sample_size"]),
                }
            )
            _append_team_history(team_history, match, stats)

        with self.repository.connect() as conn:
            for quality, usable, match_id, source_provider, fixture_id, league_name, duplicate_key, split in quality_rows:
                conn.execute(
                    """
                    UPDATE historical_matches
                    SET data_quality_score = ?,
                        usable_for_training = ?,
                        source_provider = COALESCE(NULLIF(source_provider, ''), ?),
                        external_fixture_id = COALESCE(NULLIF(external_fixture_id, ''), ?),
                        league_name = COALESCE(NULLIF(league_name, ''), ?),
                        duplicate_key = ?,
                        temporal_split = ?,
                        normalized_payload = COALESCE(normalized_payload, ?),
                        imported_at = COALESCE(imported_at, created_at),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        quality,
                        usable,
                        source_provider,
                        fixture_id,
                        league_name,
                        duplicate_key,
                        split,
                        json.dumps(normalized_payload, ensure_ascii=False, separators=(",", ":")),
                        _now_iso(),
                        match_id,
                    ),
                )
            for row in feature_rows:
                conn.execute(
                    """
                    INSERT INTO historical_features (
                        user_id, match_id, feature_set_version, temporal_split,
                        home_recent_form_5, away_recent_form_5,
                        home_goals_avg_5, away_goals_avg_5,
                        home_conceded_avg_5, away_conceded_avg_5,
                        home_xg_avg_5, away_xg_avg_5,
                        home_strength, away_strength,
                        market_implied_probability, closing_line_value,
                        data_quality_score, usable_for_training, context_match_count, created_at
                    ) VALUES (
                        NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(match_id, feature_set_version) DO UPDATE SET
                        temporal_split = excluded.temporal_split,
                        home_recent_form_5 = excluded.home_recent_form_5,
                        away_recent_form_5 = excluded.away_recent_form_5,
                        home_goals_avg_5 = excluded.home_goals_avg_5,
                        away_goals_avg_5 = excluded.away_goals_avg_5,
                        home_conceded_avg_5 = excluded.home_conceded_avg_5,
                        away_conceded_avg_5 = excluded.away_conceded_avg_5,
                        home_xg_avg_5 = excluded.home_xg_avg_5,
                        away_xg_avg_5 = excluded.away_xg_avg_5,
                        home_strength = excluded.home_strength,
                        away_strength = excluded.away_strength,
                        market_implied_probability = excluded.market_implied_probability,
                        closing_line_value = excluded.closing_line_value,
                        data_quality_score = excluded.data_quality_score,
                        usable_for_training = excluded.usable_for_training,
                        context_match_count = excluded.context_match_count,
                        created_at = excluded.created_at
                    """,
                    (
                        row["match_id"],
                        row["feature_set_version"],
                        row["temporal_split"],
                        row["home_recent_form_5"],
                        row["away_recent_form_5"],
                        row["home_goals_avg_5"],
                        row["away_goals_avg_5"],
                        row["home_conceded_avg_5"],
                        row["away_conceded_avg_5"],
                        row["home_xg_avg_5"],
                        row["away_xg_avg_5"],
                        row["home_strength"],
                        row["away_strength"],
                        row["market_implied_probability"],
                        row["closing_line_value"],
                        row["data_quality_score"],
                        row["usable_for_training"],
                        row["context_match_count"],
                        _now_iso(),
                    ),
                )
                features_written += 1
        reliability = self.rebuild_league_reliability()
        self.repository.log(
            "historicalFeatureStore",
            "Historical quality/features rebuilt.",
            payload={
                "feature_set_version": feature_set_version,
                "matches": len(matches),
                "features_written": features_written,
                "trainable": sum(1 for row in feature_rows if row["usable_for_training"]),
                "league_reliability_rows": reliability["rows_written"],
            },
        )
        return {
            "ok": True,
            "feature_set_version": feature_set_version,
            "matches_processed": len(matches),
            "features_written": features_written,
            "trainable_matches": sum(1 for row in feature_rows if row["usable_for_training"]),
            "quality_summary": self.summary(),
            "league_reliability": reliability,
        }

    def summary(self) -> dict[str, Any]:
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_matches,
                    COUNT(DISTINCT league) AS leagues,
                    COUNT(DISTINCT season) AS seasons,
                    SUM(CASE WHEN usable_for_training = 1 THEN 1 ELSE 0 END) AS trainable_matches,
                    ROUND(AVG(data_quality_score), 2) AS avg_data_quality,
                    SUM(CASE WHEN data_quality_score < 70 THEN 1 ELSE 0 END) AS consultation_only
                FROM historical_matches
                """
            ).fetchone()
            odds_count = conn.execute("SELECT COUNT(DISTINCT historical_match_id) AS count FROM historical_odds WHERE is_real = 1").fetchone()["count"]
            stats_count = conn.execute(
                f"SELECT COUNT(DISTINCT historical_match_id) AS count FROM historical_stats WHERE {_VALID_STATS_SQL}"
            ).fetchone()["count"]
            duplicates = conn.execute(
                """
                SELECT COALESCE(SUM(cnt - 1), 0) AS duplicates
                FROM (
                    SELECT duplicate_key, COUNT(*) AS cnt
                    FROM historical_matches
                    WHERE duplicate_key IS NOT NULL
                    GROUP BY duplicate_key
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()["duplicates"]
            top_leagues = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT league, season, match_count, trainable_count, avg_data_quality,
                           odds_count, stats_count, league_reliability_score, classification
                    FROM league_reliability_scores
                    ORDER BY league_reliability_score DESC, match_count DESC
                    LIMIT 8
                    """
                ).fetchall()
            ]
            weak_leagues = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT league, season, match_count, trainable_count, avg_data_quality,
                           odds_count, stats_count, league_reliability_score, classification
                    FROM league_reliability_scores
                    ORDER BY league_reliability_score ASC, match_count DESC
                    LIMIT 8
                    """
                ).fetchall()
            ]
        return {
            **dict(row),
            "matches_with_real_odds": int(odds_count or 0),
            "matches_with_stats": int(stats_count or 0),
            "duplicates_blocked": int(duplicates or 0),
            "top_leagues": top_leagues,
            "weak_leagues": weak_leagues,
        }

    def rebuild_league_reliability(self) -> dict[str, Any]:
        with self.repository.connect() as conn:
            quality_rows = [dict(row) for row in conn.execute(
                f"""
                SELECT m.league, m.season, COUNT(*) AS match_count,
                       SUM(CASE WHEN m.usable_for_training = 1 THEN 1 ELSE 0 END) AS trainable_count,
                       ROUND(AVG(m.data_quality_score), 2) AS avg_data_quality,
                       COUNT(DISTINCT o.historical_match_id) AS odds_count,
                       COUNT(DISTINCT CASE WHEN {_VALID_STATS_SQL_WITH_ALIAS} THEN s.historical_match_id END) AS stats_count
                FROM historical_matches m
                LEFT JOIN historical_odds o ON o.historical_match_id = m.id AND o.is_real = 1
                LEFT JOIN historical_stats s ON s.historical_match_id = m.id
                GROUP BY m.league, m.season
                """
            ).fetchall()]
            performance = _historical_odds_performance(conn)
            conn.execute("DELETE FROM league_reliability_scores")
            rows_written = 0
            for row in quality_rows:
                league = str(row["league"])
                season = row["season"]
                key = (league, season)
                perf = performance.get(key) or performance.get((league, None)) or {}
                score, classification, reasons = _league_reliability(row, perf)
                conn.execute(
                    """
                    INSERT INTO league_reliability_scores (
                        user_id, league, season, match_count, trainable_count, odds_count, stats_count,
                        avg_data_quality, roi_simulated, drawdown, stability_score,
                        league_reliability_score, classification, reasons_json, calculated_at
                    ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(league, season) DO UPDATE SET
                        match_count = excluded.match_count,
                        trainable_count = excluded.trainable_count,
                        odds_count = excluded.odds_count,
                        stats_count = excluded.stats_count,
                        avg_data_quality = excluded.avg_data_quality,
                        roi_simulated = excluded.roi_simulated,
                        drawdown = excluded.drawdown,
                        stability_score = excluded.stability_score,
                        league_reliability_score = excluded.league_reliability_score,
                        classification = excluded.classification,
                        reasons_json = excluded.reasons_json,
                        calculated_at = excluded.calculated_at
                    """,
                    (
                        league,
                        season,
                        int(row["match_count"] or 0),
                        int(row["trainable_count"] or 0),
                        int(row["odds_count"] or 0),
                        int(row["stats_count"] or 0),
                        float(row["avg_data_quality"] or 0),
                        float(perf.get("roi") or 0),
                        float(perf.get("drawdown") or 0),
                        float(perf.get("stability_score") or 0),
                        score,
                        classification,
                        json.dumps(reasons, ensure_ascii=False, separators=(",", ":")),
                        _now_iso(),
                    ),
                )
                rows_written += 1
        return {"ok": True, "rows_written": rows_written}

    def _load_matches(self, *, limit: int | None) -> list[dict[str, Any]]:
        query = "SELECT * FROM historical_matches ORDER BY match_date ASC, id ASC"
        params: list[Any] = []
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        with self.repository.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def _load_odds_by_match(self) -> dict[int, list[dict[str, Any]]]:
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT * FROM historical_odds ORDER BY timestamp ASC, id ASC").fetchall()
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[int(row["historical_match_id"])].append(dict(row))
        return grouped

    def _load_stats_by_match(self) -> dict[int, dict[str, Any]]:
        with self.repository.connect() as conn:
            rows = conn.execute("SELECT * FROM historical_stats").fetchall()
        return {int(row["historical_match_id"]): dict(row) for row in rows}


def get_training_context(repository: FootballResearchRepository, match_date: str | datetime, *, limit: int = 500) -> dict[str, Any]:
    cutoff = match_date.isoformat() if isinstance(match_date, datetime) else str(match_date)
    with repository.connect() as conn:
        matches = conn.execute(
            """
            SELECT id, external_id, league, season, match_date, home_team, away_team, home_goals, away_goals, data_quality_score
            FROM historical_matches
            WHERE match_date < ?
            ORDER BY match_date DESC
            LIMIT ?
            """,
            (cutoff, max(1, limit)),
        ).fetchall()
        features = conn.execute(
            """
            SELECT f.*
            FROM historical_features f
            JOIN historical_matches m ON m.id = f.match_id
            WHERE m.match_date < ?
            ORDER BY m.match_date DESC
            LIMIT ?
            """,
            (cutoff, max(1, limit)),
        ).fetchall()
    return {
        "cutoff": cutoff,
        "matches": [dict(row) for row in matches],
        "features": [dict(row) for row in features],
        "rule": "Somente registros com match_date < cutoff. Sem placar futuro, stats pós-jogo do alvo ou closing odds futuras.",
    }


def calculate_data_quality(match: dict[str, Any], *, odds: list[dict[str, Any]], stats: dict[str, Any] | None, duplicate_count: int = 1) -> int:
    score = 0
    status = str(match.get("status") or "").upper()
    if status in {"FT", "AET", "PEN", "FINISHED"} and match.get("home_goals") is not None and match.get("away_goals") is not None:
        score += 25
    if any(_is_real_odd(row) for row in odds):
        score += 25
    if stats and any(value not in (None, "") for key, value in stats.items() if key not in {"id", "user_id", "historical_match_id", "created_at", "updated_at"}):
        score += 20
    if match.get("league") and match.get("home_team") and match.get("away_team"):
        score += 15
    if duplicate_count <= 1:
        score += 15
    return min(100, score)


def _is_real_odd(row: dict[str, Any]) -> bool:
    if int(row.get("is_real") if row.get("is_real") is not None else 1) != 1:
        return False
    source = str(row.get("source") or "").lower()
    bookmaker = str(row.get("bookmaker") or "").lower()
    return "mock" not in source and "mock" not in bookmaker


def _temporal_splits(matches: list[dict[str, Any]]) -> dict[int, str]:
    dated = [(int(row["id"]), _parse_date(row.get("match_date"))) for row in matches]
    dated = [(match_id, value) for match_id, value in dated if value is not None]
    years = sorted({value.year for _, value in dated})
    if len(years) >= 3:
        train_years = set(years[:-2])
        validation_year = years[-2]
        test_year = years[-1]
        return {
            match_id: "train" if value.year in train_years else ("validation" if value.year == validation_year else "test")
            for match_id, value in dated
        }
    total = len(dated)
    splits: dict[int, str] = {}
    for index, (match_id, _) in enumerate(sorted(dated, key=lambda item: item[1])):
        ratio = index / max(1, total)
        splits[match_id] = "train" if ratio < 0.60 else ("validation" if ratio < 0.80 else "test")
    return splits


def _duplicate_counts(matches: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for match in matches:
        counts[_duplicate_key(match)] += 1
    return counts


def _duplicate_key(match: dict[str, Any]) -> str:
    source = str(match.get("source_provider") or match.get("source") or "unknown")
    external_id = str(match.get("external_fixture_id") or match.get("external_id") or "")
    if external_id:
        return f"{source}:{external_id}".lower()
    parts = [
        source,
        match.get("league"),
        match.get("season"),
        str(match.get("match_date") or "")[:19],
        match.get("home_team"),
        match.get("away_team"),
    ]
    return ":".join(" ".join(str(part or "").lower().split()) for part in parts)


def _normalized_payload(match: dict[str, Any], *, data_quality_score: int, temporal_split: str) -> dict[str, Any]:
    return {
        "source_provider": match.get("source_provider") or match.get("source"),
        "external_fixture_id": match.get("external_fixture_id") or match.get("external_id"),
        "league_id": match.get("league_id"),
        "league_name": match.get("league_name") or match.get("league"),
        "season": match.get("season"),
        "match_date": match.get("match_date"),
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "home_score": match.get("home_goals"),
        "away_score": match.get("away_goals"),
        "status": match.get("status"),
        "data_quality_score": data_quality_score,
        "usable_for_training": data_quality_score >= 70,
        "temporal_split": temporal_split,
    }


def _team_context_from_history(rows: list[dict[str, Any]], team: str) -> dict[str, Any]:
    recent = rows[-5:]
    if not recent:
        return {
            "sample_size": 0,
            "form_5": 0.0,
            "goals_for_avg_5": 0.0,
            "goals_against_avg_5": 0.0,
            "xg_avg_5": 0.0,
            "strength": 0.0,
        }
    points: list[float] = []
    goals_for: list[float] = []
    goals_against: list[float] = []
    xg: list[float] = []
    for row in recent:
        gf, ga = _team_goals(row, team)
        goals_for.append(gf)
        goals_against.append(ga)
        points.append(3 if gf > ga else (1 if gf == ga else 0))
        xg_value = row.get("team_xg")
        if xg_value is not None:
            xg.append(float(xg_value))
    form = sum(points) / max(1, len(points))
    gf_avg = sum(goals_for) / max(1, len(goals_for))
    ga_avg = sum(goals_against) / max(1, len(goals_against))
    strength = (form / 3.0 * 0.45) + (min(3.0, gf_avg) / 3.0 * 0.35) + (max(0.0, 3.0 - min(3.0, ga_avg)) / 3.0 * 0.20)
    return {
        "sample_size": len(recent),
        "form_5": round(form, 3),
        "goals_for_avg_5": round(gf_avg, 3),
        "goals_against_avg_5": round(ga_avg, 3),
        "xg_avg_5": round(sum(xg) / len(xg), 3) if xg else 0.0,
        "strength": round(strength, 4),
    }


def _append_team_history(team_history: dict[tuple[str, str], list[dict[str, Any]]], match: dict[str, Any], stats: dict[str, Any] | None) -> None:
    league = str(match["league"])
    home = str(match["home_team"])
    away = str(match["away_team"])
    home_row = dict(match)
    away_row = dict(match)
    if stats:
        home_row["team_xg"] = stats.get("xg_home")
        away_row["team_xg"] = stats.get("xg_away")
    team_history[(league, home)].append(home_row)
    team_history[(league, away)].append(away_row)


def _team_goals(row: dict[str, Any], team: str) -> tuple[float, float]:
    if str(row.get("home_team") or "").lower() == team.lower():
        return float(row.get("home_goals") or 0), float(row.get("away_goals") or 0)
    return float(row.get("away_goals") or 0), float(row.get("home_goals") or 0)


def _market_implied_probability(odds: list[dict[str, Any]]) -> float | None:
    for row in odds:
        for field in ("home_odd", "over_odd", "away_odd", "draw_odd", "under_odd"):
            odd = _safe_float(row.get(field))
            if odd and odd > 1:
                return round(1.0 / odd, 4)
    return None


def _historical_odds_performance(conn) -> dict[tuple[str, int | None], dict[str, Any]]:
    rows = conn.execute("SELECT payload_json FROM learning_events WHERE event_type = 'historical_odds_ev'").fetchall()
    by_key: dict[tuple[str, int | None], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            continue
        league = str(payload.get("league") or "")
        if not league:
            continue
        season = payload.get("season")
        key = (league, int(season) if str(season or "").isdigit() else None)
        if float(payload.get("stake_paper") or 0) > 0:
            by_key[key].append(payload)
            by_key[(league, None)].append(payload)
    result: dict[tuple[str, int | None], dict[str, Any]] = {}
    for key, items in by_key.items():
        staked = sum(float(item.get("stake_paper") or 0) for item in items)
        profit = sum(float(item.get("profit_paper") or 0) for item in items)
        curve: list[float] = []
        running = 0.0
        for item in sorted(items, key=lambda entry: str(entry.get("match_date") or "")):
            running += float(item.get("profit_paper") or 0)
            curve.append(running)
        result[key] = {
            "roi": round((profit / staked * 100.0) if staked else 0.0, 2),
            "drawdown": round(_drawdown(curve), 2),
            "stability_score": _stability_score(items),
        }
    return result


def _league_reliability(row: dict[str, Any], perf: dict[str, Any]) -> tuple[float, str, list[str]]:
    matches = int(row.get("match_count") or 0)
    trainable = int(row.get("trainable_count") or 0)
    odds = int(row.get("odds_count") or 0)
    stats = int(row.get("stats_count") or 0)
    avg_quality = float(row.get("avg_data_quality") or 0)
    roi = float(perf.get("roi") or 0)
    drawdown = float(perf.get("drawdown") or 0)
    stability = float(perf.get("stability_score") or 0)
    score = 0.0
    score += min(25.0, matches / 400.0 * 25.0)
    score += min(25.0, trainable / max(1, matches) * 25.0)
    score += min(25.0, avg_quality / 100.0 * 25.0)
    score += min(15.0, odds / max(1, matches) * 40.0)
    score += min(10.0, stats / max(1, matches) * 10.0)
    score += max(-12.0, min(10.0, roi / 4.0))
    score += min(8.0, stability)
    score -= min(12.0, drawdown / 50.0)
    score = round(max(0.0, min(100.0, score)), 2)
    reasons: list[str] = []
    if avg_quality >= 80:
        reasons.append("Qualidade média alta.")
    if odds == 0:
        reasons.append("Sem odds reais suficientes.")
    if trainable / max(1, matches) < 0.50:
        reasons.append("Poucos jogos válidos para treino principal.")
    if roi < -10:
        reasons.append("ROI paper histórico negativo.")
    if drawdown > 150:
        reasons.append("Drawdown histórico elevado.")
    classification = "Boa para operar" if score >= 75 else ("Em observação" if score >= 55 else "Evitar")
    return score, classification, reasons or ["Sem anomalia crítica na base atual."]


def _stability_score(items: list[dict[str, Any]]) -> float:
    monthly: dict[str, float] = defaultdict(float)
    for item in items:
        month = str(item.get("match_date") or "")[:7]
        monthly[month] += float(item.get("profit_paper") or 0)
    values = list(monthly.values())
    if len(values) < 2:
        return 3.0 if values else 0.0
    volatility = pstdev(values)
    return round(max(0.0, min(8.0, 8.0 - volatility / 20.0)), 2)


def _drawdown(curve: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, peak - value)
    return worst


def _parse_date(value: Any) -> datetime | None:
    try:
        text = str(value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


_VALID_STATS_SQL = """
    possession_home IS NOT NULL OR possession_away IS NOT NULL OR
    shots_home IS NOT NULL OR shots_away IS NOT NULL OR
    shots_on_home IS NOT NULL OR shots_on_away IS NOT NULL OR
    corners_home IS NOT NULL OR corners_away IS NOT NULL OR
    yellow_home IS NOT NULL OR yellow_away IS NOT NULL OR
    red_home IS NOT NULL OR red_away IS NOT NULL OR
    dangerous_attacks_home IS NOT NULL OR dangerous_attacks_away IS NOT NULL OR
    attacks_home IS NOT NULL OR attacks_away IS NOT NULL OR
    xg_home IS NOT NULL OR xg_away IS NOT NULL
"""

_VALID_STATS_SQL_WITH_ALIAS = """
    s.possession_home IS NOT NULL OR s.possession_away IS NOT NULL OR
    s.shots_home IS NOT NULL OR s.shots_away IS NOT NULL OR
    s.shots_on_home IS NOT NULL OR s.shots_on_away IS NOT NULL OR
    s.corners_home IS NOT NULL OR s.corners_away IS NOT NULL OR
    s.yellow_home IS NOT NULL OR s.yellow_away IS NOT NULL OR
    s.red_home IS NOT NULL OR s.red_away IS NOT NULL OR
    s.dangerous_attacks_home IS NOT NULL OR s.dangerous_attacks_away IS NOT NULL OR
    s.attacks_home IS NOT NULL OR s.attacks_away IS NOT NULL OR
    s.xg_home IS NOT NULL OR s.xg_away IS NOT NULL
"""
