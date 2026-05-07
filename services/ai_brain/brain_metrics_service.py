from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from src.config import Settings, load_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def _pct(part: int | float, total: int | float) -> float | None:
    if not total:
        return None
    return round((float(part) / float(total)) * 100, 2)


def _json_loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        data = json.loads(str(value))
        return data if data is not None else fallback
    except Exception:
        return fallback


def _db_path(value: str | Path | None) -> Path:
    return Path(str(value or "")).expanduser()


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path or not path.exists():
        return None
    conn = sqlite3.connect(path.as_posix(), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _scalar(conn: sqlite3.Connection | None, sql: str, default: Any = 0, params: tuple[Any, ...] = ()) -> Any:
    if conn is None:
        return default
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return default
        return row[0] if row[0] is not None else default
    except sqlite3.Error:
        return default


def _rows(conn: sqlite3.Connection | None, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if conn is None:
        return []
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _max_iso(values: list[Any]) -> str | None:
    clean = [str(value) for value in values if value]
    return max(clean) if clean else None


@dataclass(frozen=True)
class BrainDbPaths:
    research: Path
    brain: Path
    decision: Path
    usage: Path
    global_ai: Path


class BrainMetricsService:
    """Calcula o painel Cérebro IA exclusivamente com dados persistidos.

    O serviço é propositalmente tolerante a tabelas ausentes: se uma parte do
    sistema ainda não existir, retorna contadores zerados/empty state em vez de
    fabricar métricas ou quebrar o dashboard.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        research_db_file: str | Path | None = None,
        brain_db_file: str | Path | None = None,
        decision_db_file: str | Path | None = None,
        usage_db_file: str | Path | None = None,
        global_ai_db_file: str | Path | None = None,
    ):
        self.settings = settings or load_settings()
        self.paths = BrainDbPaths(
            research=_db_path(research_db_file or os.getenv("FOOTBALL_RESEARCH_DB_FILE") or "data/football_quant_research.db"),
            brain=_db_path(brain_db_file or self.settings.brain_db_file),
            decision=_db_path(decision_db_file or self.settings.decision_audit_db_file),
            usage=_db_path(usage_db_file or self.settings.usage_metrics_db_file),
            global_ai=_db_path(global_ai_db_file or os.getenv("GLOBAL_AI_DB_FILE") or "data/global_adaptive_intelligence.db"),
        )

    def metrics(self) -> dict[str, Any]:
        research = _connect(self.paths.research)
        brain = _connect(self.paths.brain)
        decision = _connect(self.paths.decision)
        usage = _connect(self.paths.usage)
        global_ai = _connect(self.paths.global_ai)
        try:
            research_metrics = self._research_metrics(research)
            brain_metrics = self._brain_metrics(brain)
            decision_metrics = self._decision_metrics(decision)
            usage_metrics = self._usage_metrics(usage)
            global_metrics = self._global_metrics(global_ai)
            performance = self._performance_metrics(research, decision)
            charts = self._charts(research, brain, decision)

            total_games = (
                research_metrics["historical_matches"]
                + brain_metrics["brain_matches"]
                + decision_metrics["distinct_matches"]
            )
            total_signals = (
                research_metrics["predictions"]
                + brain_metrics["skill_results"]
                + decision_metrics["decision_logs"]
            )
            total_backtests = research_metrics["simulation_runs"] + decision_metrics["backtest_runs"]
            total_simulations = research_metrics["simulation_results"] + global_metrics["monte_carlo_runs"]
            total_leagues = len(
                set(research_metrics["leagues"])
                | set(brain_metrics["leagues"])
                | set(decision_metrics["leagues"])
            )
            total_markets = len(
                set(research_metrics["markets"])
                | set(brain_metrics["markets"])
                | set(decision_metrics["markets"])
            )
            memory_total = (
                research_metrics["learning_events"]
                + research_metrics["rag_documents"]
                + research_metrics["rag_chunks"]
                + brain_metrics["learning_events"]
                + global_metrics["long_term_memory"]
            )
            data_coverage_games = max(total_games, research_metrics["historical_matches"])
            odds_ratio = _pct(
                research_metrics["matches_with_real_odds"],
                data_coverage_games,
            )
            maturity_score = self._maturity_score(
                historical_matches=research_metrics["historical_matches"],
                data_coverage_games=data_coverage_games,
                odds_ratio=odds_ratio,
                total_backtests=total_backtests,
                total_signals=total_signals,
                performance=performance,
                memory_total=memory_total,
            )
            status, status_reason = self._brain_status(
                historical_matches=research_metrics["historical_matches"],
                data_coverage_games=data_coverage_games,
                signals=total_signals,
                maturity_score=maturity_score,
                data_sources=self._data_sources(usage_metrics),
            )

            metrics = {
                "total_jogos_analisados": total_games,
                "total_jogos_historicos": research_metrics["historical_matches"],
                "total_sinais_registrados": total_signals,
                "total_backtests": total_backtests,
                "total_simulacoes": total_simulations,
                "total_ligas_monitoradas": total_leagues,
                "total_mercados_monitorados": total_markets,
                "taxa_acerto_historica": performance["hit_rate"],
                "ROI_simulado": performance["roi"],
                "lucro_prejuizo_simulado": performance["profit_loss"],
                "drawdown_maximo": performance["max_drawdown"],
                "brier_score_medio": research_metrics["avg_brier_score"],
                "mercados_com_melhor_performance": performance["best_markets"],
                "ligas_com_melhor_performance": performance["best_leagues"],
                "ligas_em_observacao": research_metrics["watched_leagues"],
                "sinais_bloqueados_por_risco": decision_metrics["blocked_signals"],
                "entradas_liberadas": decision_metrics["allowed_entries"] + brain_metrics["enter_decisions"],
                "entradas_rejeitadas": decision_metrics["rejected_entries"] + brain_metrics["reject_decisions"],
                "dados_com_odds_confirmadas": research_metrics["matches_with_real_odds"],
                "dados_sem_odds": max(0, data_coverage_games - research_metrics["matches_with_real_odds"]),
                "ultima_atualizacao": _max_iso(
                    [
                        research_metrics["last_update"],
                        brain_metrics["last_update"],
                        decision_metrics["last_update"],
                        usage_metrics["last_update"],
                    ]
                )
                or _now_iso(),
            }

            payload = {
                "status": status,
                "status_reason": status_reason,
                "ia_maturity_score": maturity_score,
                "ia_maturity_label": self._maturity_label(maturity_score),
                "metrics": metrics,
                "cognitive_modules": self._cognitive_modules(
                    research_metrics=research_metrics,
                    decision_metrics=decision_metrics,
                    total_backtests=total_backtests,
                    total_signals=total_signals,
                    memory_total=memory_total,
                    odds_ratio=odds_ratio,
                    data_coverage_games=data_coverage_games,
                ),
                "recommendations": self._recommendations(
                    metrics=metrics,
                    odds_ratio=odds_ratio,
                    performance=performance,
                    research_metrics=research_metrics,
                    total_backtests=total_backtests,
                ),
                "data_sources": self._data_sources(usage_metrics),
                "alerts": self._alerts(
                    metrics=metrics,
                    odds_ratio=odds_ratio,
                    usage_metrics=usage_metrics,
                    research_metrics=research_metrics,
                    performance=performance,
                ),
                "charts": charts,
                "raw_counts": {
                    "research": research_metrics,
                    "brain": brain_metrics,
                    "decision": decision_metrics,
                    "usage": usage_metrics,
                    "global": global_metrics,
                },
            }
            return payload
        finally:
            for conn in (research, brain, decision, usage, global_ai):
                if conn is not None:
                    conn.close()

    def summary(self) -> dict[str, Any]:
        data = self.metrics()
        metrics = data["metrics"]
        problems = []
        next_steps = []
        if data["status"] in {"Dados insuficientes", "Offline"}:
            problems.append(data.get("status_reason") or "Base ainda não sustenta leitura completa.")
        if metrics["ROI_simulado"] is not None and metrics["ROI_simulado"] < 0:
            problems.append("ROI simulado negativo detectado.")
            next_steps.append("Usar modo conservador e revisar mercados com perda.")
        if metrics["dados_sem_odds"] > 0:
            problems.append("Parte relevante da base ainda não possui odds reais confirmadas.")
            next_steps.append("Priorizar importação de odds reais por fixture/liga/data.")
        if metrics["total_backtests"] < 30:
            next_steps.append("Rodar mais backtests salvos por liga e mercado.")
        if not next_steps:
            next_steps.append("Manter coleta, backtests e revisão por liga/mercado.")
        text = (
            f"Cérebro IA em status {data['status']} com maturidade {data['ia_maturity_score']}/100 "
            f"({data['ia_maturity_label']}). "
            f"Base real: {metrics['total_jogos_analisados']} jogos analisados, "
            f"{metrics['total_sinais_registrados']} sinais e {metrics['total_backtests']} backtests. "
            f"Principais pontos: {'; '.join(problems) if problems else 'sem alerta crítico no momento'}. "
            f"Próximos passos: {'; '.join(next_steps)}"
        )
        return {
            "summary": text,
            "generated_by": "local_metrics",
            "gemini_available": bool(self.settings.gemini_api_key),
            "metrics_updated_at": metrics["ultima_atualizacao"],
        }

    def _research_metrics(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        historical_matches = _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_matches"))
        matches_with_odds = _safe_int(
            _scalar(conn, "SELECT COUNT(DISTINCT historical_match_id) FROM historical_odds WHERE COALESCE(is_real, 1) = 1")
        )
        stats_count = _safe_int(
            _scalar(
                conn,
                """
                SELECT COUNT(DISTINCT historical_match_id)
                FROM historical_stats
                WHERE possession_home IS NOT NULL OR shots_home IS NOT NULL OR shots_on_home IS NOT NULL
                   OR corners_home IS NOT NULL OR yellow_home IS NOT NULL OR dangerous_attacks_home IS NOT NULL
                   OR xg_home IS NOT NULL
                """,
            )
        )
        learning_events = _safe_int(_scalar(conn, "SELECT COUNT(*) FROM learning_events"))
        brier_values = []
        if conn is not None and _table_exists(conn, "learning_events"):
            for row in _rows(conn, "SELECT payload_json FROM learning_events WHERE payload_json LIKE '%brier_score%' LIMIT 5000"):
                payload = _json_loads(row.get("payload_json"), {})
                value = _safe_float(payload.get("brier_score"), None) if isinstance(payload, dict) else None
                if value is not None:
                    brier_values.append(value)
        leagues = [
            str(row["league"])
            for row in _rows(conn, "SELECT DISTINCT league FROM historical_matches WHERE league IS NOT NULL AND TRIM(league) <> ''")
        ]
        markets = [
            str(row["market"])
            for row in _rows(
                conn,
                """
                SELECT DISTINCT market FROM historical_odds WHERE market IS NOT NULL AND TRIM(market) <> ''
                UNION
                SELECT DISTINCT market FROM predictions WHERE market IS NOT NULL AND TRIM(market) <> ''
                """,
            )
        ]
        watched = _rows(
            conn,
            """
            SELECT league, season, classification, league_reliability_score, avg_data_quality, odds_count, stats_count
            FROM league_reliability_scores
            WHERE classification IS NOT NULL AND classification <> 'Boa para operar'
            ORDER BY league_reliability_score ASC, match_count DESC
            LIMIT 8
            """,
        )
        return {
            "historical_matches": historical_matches,
            "historical_features": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_features")),
            "historical_odds": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_odds")),
            "historical_corners": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_corners")),
            "historical_cards": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_cards")),
            "historical_asian_lines": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM historical_asian_lines")),
            "market_pressure_snapshots": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM market_pressure_snapshots")),
            "referee_profiles": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM referee_profiles")),
            "live_market_movements": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM live_market_movements")),
            "matches_with_real_odds": matches_with_odds,
            "matches_with_stats": stats_count,
            "predictions": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM predictions")),
            "simulation_runs": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM simulation_runs")),
            "simulation_results": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM simulation_results")),
            "learning_events": learning_events,
            "rag_documents": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM rag_documents")),
            "rag_chunks": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM rag_chunks")),
            "avg_brier_score": round(sum(brier_values) / len(brier_values), 5) if brier_values else None,
            "leagues": leagues,
            "markets": markets,
            "watched_leagues": watched,
            "last_update": _max_iso(
                [
                    _scalar(conn, "SELECT MAX(COALESCE(imported_at, created_at, updated_at)) FROM historical_matches", None),
                    _scalar(conn, "SELECT MAX(created_at) FROM learning_events", None),
                    _scalar(conn, "SELECT MAX(created_at) FROM simulation_results", None),
                    _scalar(conn, "SELECT MAX(created_at) FROM football_research_logs", None),
                ]
            ),
        }

    def _brain_metrics(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        leagues = [
            str(row["league"])
            for row in _rows(conn, "SELECT DISTINCT league FROM brain_matches WHERE league IS NOT NULL AND TRIM(league) <> ''")
        ]
        markets = [
            str(row["market"])
            for row in _rows(conn, "SELECT DISTINCT market FROM brain_skill_results WHERE market IS NOT NULL AND TRIM(market) <> ''")
        ]
        return {
            "brain_matches": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_matches")),
            "live_snapshots": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_live_snapshots")),
            "pregame_watchlist": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_pregame_watchlist")),
            "learning_events": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_learning_events")),
            "skill_results": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_skill_results")),
            "enter_decisions": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM brain_skill_results WHERE UPPER(decision) LIKE '%ENTRA%'")),
            "reject_decisions": _safe_int(
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM brain_skill_results WHERE UPPER(decision) LIKE '%NAO%' OR UPPER(decision) LIKE '%NÃO%' OR UPPER(decision) LIKE '%SAI%'",
                )
            ),
            "leagues": leagues,
            "markets": markets,
            "last_update": _max_iso(
                [
                    _scalar(conn, "SELECT MAX(last_seen_at) FROM brain_matches", None),
                    _scalar(conn, "SELECT MAX(captured_at) FROM brain_live_snapshots", None),
                    _scalar(conn, "SELECT MAX(captured_at) FROM brain_skill_results", None),
                    _scalar(conn, "SELECT MAX(recorded_at) FROM brain_pregame_watchlist", None),
                ]
            ),
        }

    def _decision_metrics(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        leagues = [
            str(row["league"])
            for row in _rows(conn, "SELECT DISTINCT league FROM decision_logs WHERE league IS NOT NULL AND TRIM(league) <> ''")
        ]
        markets = [
            str(row["market"])
            for row in _rows(conn, "SELECT DISTINCT market FROM decision_logs WHERE market IS NOT NULL AND TRIM(market) <> ''")
        ]
        blocked = _safe_int(_scalar(conn, "SELECT COUNT(*) FROM decision_logs WHERE entry_allowed = 0"))
        return {
            "decision_logs": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM decision_logs")),
            "distinct_matches": _safe_int(_scalar(conn, "SELECT COUNT(DISTINCT match_id) FROM decision_logs")),
            "allowed_entries": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM decision_logs WHERE entry_allowed = 1")),
            "blocked_signals": blocked,
            "rejected_entries": blocked,
            "backtest_runs": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM backtest_runs")),
            "leagues": leagues,
            "markets": markets,
            "last_update": _max_iso(
                [
                    _scalar(conn, "SELECT MAX(created_at) FROM decision_logs", None),
                    _scalar(conn, "SELECT MAX(created_at) FROM backtest_runs", None),
                ]
            ),
        }

    def _usage_metrics(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        services = {}
        if conn is not None and _table_exists(conn, "service_usage_totals"):
            for row in _rows(conn, "SELECT * FROM service_usage_totals"):
                services[str(row["service"])] = row
        last_update = None
        for row in services.values():
            last_update = _max_iso([last_update, row.get("last_request_at")])
        return {"services": services, "last_update": last_update}

    def _global_metrics(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        return {
            "monte_carlo_runs": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM monte_carlo_runs")),
            "long_term_memory": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM long_term_memory")),
            "agent_trust_scores": _safe_int(_scalar(conn, "SELECT COUNT(*) FROM agent_trust_scores")),
        }

    def _performance_metrics(self, research: sqlite3.Connection | None, decision: sqlite3.Connection | None) -> dict[str, Any]:
        sim_rows = _rows(
            research,
            """
            SELECT s.market, m.league, s.offered_odd, s.expected_value, s.result, s.stake, s.profit_loss, s.created_at
            FROM simulation_results s
            LEFT JOIN historical_matches m ON m.id = s.historical_match_id
            ORDER BY s.created_at ASC, s.id ASC
            """,
        )
        settled = [row for row in sim_rows if str(row.get("result") or "").upper() not in {"", "SKIPPED"}]
        stake_sum = sum(float(row.get("stake") or 0) for row in settled)
        profit_sum = sum(float(row.get("profit_loss") or 0) for row in settled)
        wins = sum(1 for row in settled if float(row.get("profit_loss") or 0) > 0)
        max_drawdown = self._drawdown([float(row.get("profit_loss") or 0) for row in settled])
        best_markets = self._group_performance(settled, "market")
        best_leagues = self._group_performance(settled, "league")
        return {
            "settled_entries": len(settled),
            "hit_rate": round((wins / len(settled)) * 100, 2) if settled else None,
            "roi": round((profit_sum / stake_sum) * 100, 2) if stake_sum > 0 else None,
            "profit_loss": round(profit_sum, 2) if settled else None,
            "max_drawdown": max_drawdown if settled else None,
            "best_markets": best_markets,
            "best_leagues": best_leagues,
        }

    def _charts(
        self,
        research: sqlite3.Connection | None,
        brain: sqlite3.Connection | None,
        decision: sqlite3.Connection | None,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "roi_evolution": self._roi_series(research),
            "signals_by_day": self._signals_by_day(research, brain, decision),
            "hit_rate_evolution": self._hit_rate_series(research),
            "drawdown_evolution": self._drawdown_series(research),
            "imports_by_day": _rows(
                research,
                """
                SELECT SUBSTR(COALESCE(imported_at, created_at), 1, 10) AS label, COUNT(*) AS value
                FROM historical_matches
                GROUP BY label
                ORDER BY label DESC
                LIMIT 18
                """,
            )[::-1],
            "blocked_by_day": _rows(
                decision,
                """
                SELECT SUBSTR(created_at, 1, 10) AS label, COUNT(*) AS value
                FROM decision_logs
                WHERE entry_allowed = 0
                GROUP BY label
                ORDER BY label DESC
                LIMIT 18
                """,
            )[::-1],
            "performance_by_market": self._chart_performance_by_market(research),
        }

    def _data_sources(self, usage_metrics: dict[str, Any]) -> list[dict[str, Any]]:
        services = usage_metrics.get("services") or {}

        def usage_for(*names: str) -> dict[str, Any]:
            for name in names:
                if name in services:
                    return services[name]
            return {}

        def row(name: str, configured: bool, service_key: str, *, fallback_active: bool = False, notes: str = "") -> dict[str, Any]:
            usage = usage_for(service_key, service_key.replace("-", "_"))
            last_error = usage.get("last_error") if isinstance(usage, dict) else None
            status = "ativa" if configured else "inativa"
            if last_error:
                status = "erro_recente" if configured else "inativa"
            return {
                "name": name,
                "status": status,
                "configured": bool(configured),
                "last_success": usage.get("last_request_at") if usage and int(usage.get("success_requests") or 0) > 0 else None,
                "last_error": last_error,
                "latency_ms": None,
                "requests": int(usage.get("requests") or 0) if usage else 0,
                "fallback_active": bool(fallback_active),
                "notes": notes,
            }

        local_cache_active = any(path.exists() for path in (self.paths.research, self.paths.brain, self.paths.decision))
        return [
            row("API-Football", bool(self.settings.api_football_key), "api_football"),
            row("Supabase", bool(self.settings.supabase_url and self.settings.supabase_service_role_key), "supabase"),
            row("Gemini", bool(self.settings.gemini_api_key), "gemini"),
            row("The Odds API", bool(self.settings.odds_api_io_key), "odds_api_io"),
            row("iSports", bool(os.getenv("ISPORTS_API_KEY")), "isports"),
            row("Cache local", local_cache_active, "local_cache", notes="SQLite local"),
            row("Fallback mock", bool(os.getenv("FOOTBALL_RESEARCH_MOCKS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}), "mock", fallback_active=True),
        ]

    def _recommendations(
        self,
        *,
        metrics: dict[str, Any],
        odds_ratio: float | None,
        performance: dict[str, Any],
        research_metrics: dict[str, Any],
        total_backtests: int,
    ) -> list[dict[str, Any]]:
        recs: list[dict[str, Any]] = []
        roi = metrics.get("ROI_simulado")
        if roi is not None and roi < 0:
            recs.append({"severity": "alta", "title": "Ativar modo conservador", "reason": "ROI simulado negativo detectado."})
        if odds_ratio is None or odds_ratio < 30:
            recs.append(
                {
                    "severity": "media",
                    "title": "Aumentar cobertura de odds reais",
                    "reason": "Menos de 30% dos jogos históricos possuem odds reais confirmadas.",
                }
            )
        if total_backtests < 30:
            recs.append({"severity": "media", "title": "Rodar mais backtests", "reason": "Há poucos backtests salvos para comparar ligas e mercados com estabilidade."})
        weak = research_metrics.get("watched_leagues") or []
        if weak:
            league = weak[0]
            recs.append(
                {
                    "severity": "media",
                    "title": f"Revisar liga {league.get('league')}",
                    "reason": f"Classificação atual: {league.get('classification') or 'em observação'}.",
                }
            )
        best_market = (performance.get("best_markets") or [{}])[0]
        if best_market.get("roi") is not None and float(best_market["roi"]) > 0:
            recs.append(
                {
                    "severity": "baixa",
                    "title": f"Mercado {best_market.get('name')} performou melhor",
                    "reason": "Use como recorte de estudo, não como promessa de resultado.",
                }
            )
        if not recs:
            recs.append({"severity": "baixa", "title": "Continuar coleta disciplinada", "reason": "Sem alerta crítico com os dados disponíveis."})
        return recs

    def _alerts(
        self,
        *,
        metrics: dict[str, Any],
        odds_ratio: float | None,
        usage_metrics: dict[str, Any],
        research_metrics: dict[str, Any],
        performance: dict[str, Any],
    ) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        if metrics["total_jogos_analisados"] <= 0:
            alerts.append({"level": "critical", "message": "Sem jogos analisados na base local."})
        if research_metrics["historical_matches"] <= 0 and metrics["total_jogos_analisados"] > 0:
            alerts.append(
                {
                    "level": "warning",
                    "message": "Há jogos/sinais no scanner, mas a base histórica de 3 anos ainda não está consolidada neste ambiente.",
                }
            )
        if odds_ratio is None or odds_ratio < 30:
            alerts.append({"level": "warning", "message": "Odds reais confirmadas abaixo de 30% da base histórica."})
        if research_metrics["matches_with_stats"] <= 0 and research_metrics["historical_matches"] > 0:
            stats_message = "Jogos históricos existem, mas estatísticas detalhadas ainda estão ausentes."
            if research_metrics["historical_features"] > 0:
                stats_message = (
                    "Estatísticas detalhadas ainda estão ausentes; "
                    "as features atuais usam placar, forma e qualidade dos dados."
                )
            alerts.append({"level": "warning", "message": stats_message})
        if performance["roi"] is not None and performance["roi"] < 0:
            alerts.append({"level": "warning", "message": "ROI simulado negativo detectado."})
        services = usage_metrics.get("services") or {}
        for service, row in services.items():
            if row.get("last_error"):
                alerts.append({"level": "warning", "message": f"{service}: erro recente registrado.", "detail": str(row.get("last_error"))[:160]})
        if not (self.settings.supabase_url and self.settings.supabase_service_role_key):
            alerts.append({"level": "info", "message": "Supabase não configurado neste ambiente; usando memória SQLite local."})
        return alerts[:10]

    def _cognitive_modules(
        self,
        *,
        research_metrics: dict[str, Any],
        decision_metrics: dict[str, Any],
        total_backtests: int,
        total_signals: int,
        memory_total: int,
        odds_ratio: float | None,
        data_coverage_games: int,
    ) -> list[dict[str, Any]]:
        historical = min(100.0, (data_coverage_games / 10000) * 100)
        stats = _pct(research_metrics["historical_features"], data_coverage_games) or 0.0
        explain = _pct(research_metrics["predictions"] + decision_metrics["decision_logs"], max(total_signals, 1)) or 0.0
        historical_detail = (
            f"{research_metrics['historical_matches']} jogos históricos"
            if research_metrics["historical_matches"] > 0
            else f"{data_coverage_games} jogos analisados"
        )
        markets_text = " ".join(str(item).lower() for item in research_metrics.get("markets") or [])
        corners_rows = int(research_metrics.get("historical_corners") or 0)
        cards_rows = int(research_metrics.get("historical_cards") or 0)
        asian_rows = int(research_metrics.get("historical_asian_lines") or 0)
        pressure_rows = int(research_metrics.get("market_pressure_snapshots") or 0)
        movement_rows = int(research_metrics.get("live_market_movements") or 0)
        referee_rows = int(research_metrics.get("referee_profiles") or 0)
        corners_progress = min(100.0, (corners_rows / 2000) * 100)
        if "corner" in markets_text or "escanteio" in markets_text:
            corners_progress = max(corners_progress, 15.0)
        cards_progress = min(100.0, ((cards_rows + referee_rows) / 2000) * 100)
        if "card" in markets_text or "cart" in markets_text:
            cards_progress = max(cards_progress, 15.0)
        asian_progress = min(100.0, ((asian_rows + movement_rows) / 2000) * 100)
        if "asian" in markets_text or "handicap" in markets_text or "asi" in markets_text:
            asian_progress = max(asian_progress, 15.0)
        pressure_progress = min(100.0, ((pressure_rows + movement_rows + decision_metrics["decision_logs"]) / 5000) * 100)
        return [
            {"name": "Dados históricos", "progress": round(historical, 1), "detail": historical_detail},
            {"name": "Odds e mercados", "progress": round(float(odds_ratio or 0), 1), "detail": f"{research_metrics['matches_with_real_odds']} jogos com odds"},
            {"name": "Estatística", "progress": round(min(100.0, stats), 1), "detail": f"{research_metrics['historical_features']} features"},
            {"name": "Backtesting", "progress": round(min(100.0, (total_backtests / 100) * 100), 1), "detail": f"{total_backtests} runs"},
            {"name": "Gestão de risco", "progress": round(min(100.0, (decision_metrics['decision_logs'] / 200) * 100), 1), "detail": f"{decision_metrics['blocked_signals']} bloqueios"},
            {"name": "Memória IA", "progress": round(min(100.0, (memory_total / 1000) * 100), 1), "detail": f"{memory_total} registros"},
            {"name": "Explicabilidade", "progress": round(min(100.0, explain), 1), "detail": f"{research_metrics['predictions'] + decision_metrics['decision_logs']} explicações/logs"},
            {"name": "Corners Intelligence", "progress": round(corners_progress, 1), "detail": f"{corners_rows} linhas de escanteios"},
            {"name": "Referee Intelligence", "progress": round(cards_progress, 1), "detail": f"{cards_rows} cartões · {referee_rows} árbitros"},
            {"name": "Asian Market Intelligence", "progress": round(asian_progress, 1), "detail": f"{asian_rows} linhas asiáticas"},
            {"name": "Pressure Engine", "progress": round(pressure_progress, 1), "detail": f"{pressure_rows} snapshots de pressão"},
            {"name": "Momentum Engine", "progress": round(min(100.0, ((pressure_rows + movement_rows) / 5000) * 100), 1), "detail": f"{movement_rows} movimentos live"},
        ]

    def _maturity_score(
        self,
        *,
        historical_matches: int,
        data_coverage_games: int,
        odds_ratio: float | None,
        total_backtests: int,
        total_signals: int,
        performance: dict[str, Any],
        memory_total: int,
    ) -> int:
        score = 0
        if historical_matches > 1000:
            score += 20
        elif data_coverage_games >= 1000:
            score += 12
        elif data_coverage_games >= 100:
            score += 6
        if odds_ratio is not None and odds_ratio >= 30:
            score += 20
        elif odds_ratio is not None and odds_ratio > 0:
            score += 6
        if total_backtests > 0:
            score += 20
        if total_signals >= 100:
            score += 15
        if performance.get("best_leagues") or performance.get("best_markets"):
            score += 15
        if memory_total > 0:
            score += 10
        return min(100, score)

    def _maturity_label(self, score: int) -> str:
        if score >= 85:
            return "avançada"
        if score >= 65:
            return "alta"
        if score >= 40:
            return "média"
        return "baixa"

    def _brain_status(
        self,
        *,
        historical_matches: int,
        data_coverage_games: int,
        signals: int,
        maturity_score: int,
        data_sources: list[dict[str, Any]],
    ) -> tuple[str, str]:
        if not any(path.exists() for path in (self.paths.research, self.paths.brain, self.paths.decision)):
            return "Offline", "Nenhum banco local do ApexGol foi encontrado."
        core_sources = {"API-Football", "Supabase", "Cache local", "Fallback mock"}
        has_operational_source = any(
            item.get("status") == "ativa" and item.get("name") in core_sources
            for item in data_sources
        )
        if not has_operational_source and data_coverage_games <= 0:
            return "Offline", "Sem fonte central ativa e sem base local para fallback."
        if data_coverage_games < 100 or signals < 20:
            return "Dados insuficientes", "Ainda há poucos jogos/sinais persistidos para leitura madura."
        if maturity_score >= 70 and self.settings.supabase_url and self.settings.supabase_service_role_key:
            return "Operacional", "Base, sinais e Supabase configurados."
        if historical_matches <= 0:
            return "Aprendendo", "Scanner e sinais ativos; falta consolidar a base histórica local de 3 anos."
        return "Aprendendo", "Base local ativa; continue importando odds, backtests e memória Supabase."

    def _group_performance(self, rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(key) or "Sem dado")].append(row)
        result: list[dict[str, Any]] = []
        for name, items in groups.items():
            stake = sum(float(item.get("stake") or 0) for item in items)
            profit = sum(float(item.get("profit_loss") or 0) for item in items)
            if stake <= 0:
                continue
            wins = sum(1 for item in items if float(item.get("profit_loss") or 0) > 0)
            result.append(
                {
                    "name": name,
                    "entries": len(items),
                    "hit_rate": round((wins / len(items)) * 100, 2),
                    "roi": round((profit / stake) * 100, 2),
                    "profit_loss": round(profit, 2),
                }
            )
        result.sort(key=lambda item: (item["roi"], item["entries"]), reverse=True)
        return result[:8]

    def _drawdown(self, profits: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for profit in profits:
            equity += profit
            peak = max(peak, equity)
            max_drawdown = min(max_drawdown, equity - peak)
        return round(abs(max_drawdown), 2)

    def _roi_series(self, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
        rows = _rows(conn, "SELECT stake, profit_loss, created_at FROM simulation_results ORDER BY created_at ASC, id ASC")
        stake_total = 0.0
        profit_total = 0.0
        series: list[dict[str, Any]] = []
        for row in rows:
            stake_total += float(row.get("stake") or 0)
            profit_total += float(row.get("profit_loss") or 0)
            series.append(
                {
                    "label": str(row.get("created_at") or "")[:10],
                    "value": round((profit_total / stake_total) * 100, 2) if stake_total > 0 else None,
                    "profit": round(profit_total, 2),
                }
            )
        return series[-40:]

    def _hit_rate_series(self, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
        rows = _rows(conn, "SELECT result, profit_loss, created_at FROM simulation_results ORDER BY created_at ASC, id ASC")
        settled = 0
        wins = 0
        series = []
        for row in rows:
            if str(row.get("result") or "").upper() in {"", "SKIPPED"}:
                continue
            settled += 1
            if float(row.get("profit_loss") or 0) > 0:
                wins += 1
            series.append({"label": str(row.get("created_at") or "")[:10], "value": round((wins / settled) * 100, 2)})
        return series[-40:]

    def _drawdown_series(self, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
        rows = _rows(conn, "SELECT profit_loss, created_at FROM simulation_results ORDER BY created_at ASC, id ASC")
        equity = 0.0
        peak = 0.0
        series = []
        for row in rows:
            equity += float(row.get("profit_loss") or 0)
            peak = max(peak, equity)
            series.append({"label": str(row.get("created_at") or "")[:10], "value": round(abs(min(0.0, equity - peak)), 2)})
        return series[-40:]

    def _signals_by_day(
        self,
        research: sqlite3.Connection | None,
        brain: sqlite3.Connection | None,
        decision: sqlite3.Connection | None,
    ) -> list[dict[str, Any]]:
        counts: dict[str, int] = defaultdict(int)
        for row in _rows(research, "SELECT SUBSTR(created_at, 1, 10) AS label, COUNT(*) AS value FROM predictions GROUP BY label"):
            counts[str(row["label"])] += int(row["value"] or 0)
        for row in _rows(brain, "SELECT SUBSTR(captured_at, 1, 10) AS label, COUNT(*) AS value FROM brain_skill_results GROUP BY label"):
            counts[str(row["label"])] += int(row["value"] or 0)
        for row in _rows(decision, "SELECT SUBSTR(created_at, 1, 10) AS label, COUNT(*) AS value FROM decision_logs GROUP BY label"):
            counts[str(row["label"])] += int(row["value"] or 0)
        return [{"label": key, "value": counts[key]} for key in sorted(counts.keys())[-30:]]

    def _chart_performance_by_market(self, conn: sqlite3.Connection | None) -> list[dict[str, Any]]:
        rows = _rows(
            conn,
            """
            SELECT market, SUM(stake) AS stake, SUM(profit_loss) AS profit, COUNT(*) AS entries
            FROM simulation_results
            WHERE result IS NOT NULL AND UPPER(result) <> 'SKIPPED'
            GROUP BY market
            ORDER BY profit DESC
            LIMIT 10
            """,
        )
        chart = []
        for row in rows:
            stake = float(row.get("stake") or 0)
            chart.append(
                {
                    "label": row.get("market") or "Sem mercado",
                    "value": round((float(row.get("profit") or 0) / stake) * 100, 2) if stake > 0 else None,
                    "entries": int(row.get("entries") or 0),
                }
            )
        return chart
