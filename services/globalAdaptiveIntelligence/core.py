from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging
import os
from typing import Any

from services.footballQuantAiSkill import get_football_quant_ai_skill
from src.config import load_settings

from .alerts.alert_service import AlertService
from .backtesting.backtesting_engine import GlobalBacktestingEngine
from .config import load_global_adaptive_settings
from .data_sources.registry import DataSourceRegistryService
from .ensemble.ensemble_prediction_service import EnsemblePredictionService
from .evaluation.anomaly_detection_service import AnomalyDetectionService
from .evaluation.drift_detection_service import DriftDetectionService
from .evaluation.market_regime_service import MarketRegimeService
from .explainability.explainability_service import ExplainabilityService
from .feature_engineering.feature_engineering_service import FeatureEngineeringService
from .governance.governance_service import GovernanceService
from .memory.long_term_memory_service import LongTermMemoryService
from .meta_learning.meta_learning_service import MetaLearningService
from .models.market_efficiency_model import MarketEfficiencyModel
from .models.mean_reversion_model import MeanReversionModel
from .models.momentum_model import MomentumModel
from .models.time_series_baseline_model import TimeSeriesBaselineModel
from .monte_carlo.monte_carlo_engine import MonteCarloEngine
from .multi_agent.agents import (
    DataQualityAgent,
    DriftAgent,
    EvaluationAgent,
    ExplainabilityAgent,
    OddsAgent,
    PatternAgent,
    RiskAgent,
    StatsAgent,
    StrategyAgent,
    SupervisorAgent,
    ValueAgent,
)
from .multi_agent.consensus_engine import ConsensusEngine
from .rag.rag_knowledge_service import GlobalRagKnowledgeService
from .reports.report_service import ReportService
from .repository import GlobalAdaptiveRepository
from .risk.global_risk_service import GlobalRiskService
from .sports.football_adapter import FootballAdapter
from .strategy_lab.evolutionary_strategy_engine import EvolutionaryStrategyEngine

logger = logging.getLogger(__name__)


class GlobalAdaptiveIntelligencePlatform:
    def __init__(self):
        self.settings = load_global_adaptive_settings()
        self.football_skill = get_football_quant_ai_skill()
        self.repository = GlobalAdaptiveRepository(self.settings.db_file)
        self.data_sources = DataSourceRegistryService(self.repository)
        self.data_sources.seed()
        self.football = FootballAdapter()
        self.features = FeatureEngineeringService(self.repository, self.football)
        self.time_series_model = TimeSeriesBaselineModel()
        self.momentum_model = MomentumModel()
        self.mean_reversion_model = MeanReversionModel()
        self.market_efficiency_model = MarketEfficiencyModel()
        self.ensemble = EnsemblePredictionService(self.repository)
        self.meta = MetaLearningService(self.repository)
        self.backtesting = GlobalBacktestingEngine()
        self.monte_carlo = MonteCarloEngine()
        self.risk = GlobalRiskService()
        self.memory = LongTermMemoryService(self.repository)
        self.rag = GlobalRagKnowledgeService(self.memory)
        self.drift = DriftDetectionService()
        self.regime = MarketRegimeService()
        self.anomaly = AnomalyDetectionService()
        self.evolution = EvolutionaryStrategyEngine(self.repository)
        self.governance = GovernanceService(self.repository)
        self.alerts = AlertService()
        self.explainability = ExplainabilityService()
        self.reports = ReportService()
        self.consensus = ConsensusEngine()
        self.agents = [
            DataQualityAgent(),
            StatsAgent(),
            OddsAgent(),
            ValueAgent(),
            RiskAgent(),
            PatternAgent(),
            DriftAgent(),
            StrategyAgent(),
            EvaluationAgent(),
            ExplainabilityAgent(),
            SupervisorAgent(),
        ]

    def _research_health_snapshot(self) -> dict[str, Any]:
        try:
            payload = self.football_skill.health()
        except Exception as exc:
            return {
                "ok": False,
                "db_file": getattr(self.football_skill.settings, "db_file", "data/football_quant_research.db"),
                "counts": {},
                "sources": [],
                "supabase": {
                    "enabled": False,
                    "schema_mode": "unavailable",
                    "last_error": str(exc),
                    "available_tables": {},
                    "last_hydrate_result": {},
                },
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "db_file": getattr(self.football_skill.settings, "db_file", "data/football_quant_research.db"),
                "counts": {},
                "sources": [],
                "supabase": {
                    "enabled": False,
                    "schema_mode": "unavailable",
                    "last_error": f"payload_invalido:{type(payload).__name__}",
                    "available_tables": {},
                    "last_hydrate_result": {},
                },
            }
        payload["counts"] = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        payload["sources"] = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        payload["supabase"] = payload.get("supabase") if isinstance(payload.get("supabase"), dict) else {
            "enabled": False,
            "schema_mode": "unavailable",
            "last_error": "supabase_status_invalido",
            "available_tables": {},
            "last_hydrate_result": {},
        }
        payload["supabase"].setdefault("available_tables", {})
        payload["supabase"].setdefault("last_hydrate_result", {})
        payload["supabase"].setdefault("schema_mode", "unavailable")
        return payload

    def _live_source_runtime_snapshot(self) -> list[dict[str, Any]]:
        settings = load_settings()
        isports_key = (os.getenv("ISPORTS_API_KEY") or "").strip()
        odds_api_key = (os.getenv("ODDS_API_KEY") or os.getenv("ODDS_API_IO_KEY") or "").strip()
        return [
            {
                "id": "api_football",
                "label": "API-Football",
                "configured": bool(settings.api_football_key),
                "status": "ready" if settings.api_football_key else "missing_key",
                "base_url": settings.api_football_base_url,
                "role": "Fixtures, live stats e odds principais",
                "markets": ["Goals", "1X2", "estatisticas ao vivo"],
                "coverage_note": "Fonte principal para scanner, leitura live e fixtures operacionais.",
            },
            {
                "id": "espn",
                "label": "ESPN Scoreboard",
                "configured": True,
                "status": "ready",
                "base_url": settings.espn_site_api_base_url,
                "role": "Fallback global de placar e estatisticas leves",
                "markets": ["scoreboard", "shots", "corners", "cards", "1X2 basico"],
                "coverage_note": (
                    f"Timeout {settings.espn_timeout}s, retries {settings.espn_max_retries}. "
                    "Mantem a leitura viva quando o provider principal oscila."
                ),
            },
            {
                "id": "isports",
                "label": "iSports Odds",
                "configured": bool(isports_key),
                "status": "ready" if isports_key else "missing_key",
                "base_url": (os.getenv("ISPORTS_API_BASE_URL") or "http://api.isportsapi.com").rstrip("/"),
                "role": "Odds complementares reais para linhas principais",
                "markets": [
                    "1X2",
                    "Asian Handicap",
                    "Over/Under",
                    "Asian HT",
                    "Over/Under HT",
                ],
                "coverage_note": "Usado como complemento de mercado via companyID 8 (Bet365), sem mockar linhas ausentes.",
            },
            {
                "id": "odds_api_io",
                "label": "Odds-API.io",
                "configured": bool(odds_api_key),
                "status": "ready" if odds_api_key else "missing_key",
                "base_url": settings.odds_api_io_base_url,
                "role": "Cobertura complementar multi-bookmaker",
                "markets": ["Goals", "1X2", "totais", "live odds"],
                "coverage_note": "Enriquecimento de odds no scanner principal quando houver chave ativa.",
            },
        ]

    def audit_report(self) -> dict[str, Any]:
        discovery = self.football_skill.discovery.scan()
        return {
            "existing_stack": {
                "backend": "Python + FastAPI + SQLite + Telegram",
                "frontend": "HTML/CSS/JS renderizado pelo backend",
                "supabase": "Integração REST opcional, sem auth Supabase/RLS ativos hoje",
                "auth": "Sessão multiusuário local via portal.db",
                "tests": "JS Playwright legado + novos testes Python em andamento",
            },
            "reused_modules": [
                "src/main.py",
                "src/dashboard.py",
                "src/portal.py",
                "src/portal_web.py",
                "src/integrations/supabase.py",
                "src/intelligence/football_brain.py",
                "services/footballQuantAiSkill/*",
            ],
            "created_modules": [
                "services/globalAdaptiveIntelligence/*",
                "src/global_ai_router.py",
                "migrations/*global_adaptive*",
                "tests/football_quant_ai/*",
                "docs/*global_adaptive*",
            ],
            "risks": [
                "Projeto atual não usa React/Node; páginas novas seguem padrão FastAPI server-rendered.",
                "Supabase hoje não tem padrão local de RLS para reaproveitar sem inventar autenticação nova.",
                "APIs externas podem ficar indisponíveis; mocks/fallbacks seguem obrigatórios.",
            ],
            "discovery": discovery,
            "global_snapshot": self.repository.snapshot(),
        }

    def _build_context(self, event_id: int, market: str, offered_odd: float | None = None) -> dict[str, Any]:
        event_row = self.football.skill.repository.get_historical_match(int(event_id)) or {}
        prediction = self.football.runPrediction(event_id, market=market, offered_odd=offered_odd)
        explanation = prediction.get("explanation") or {}
        context = {
            "event_id": event_id,
            "market": market,
            "league": event_row.get("league"),
            "season": event_row.get("season"),
            "estimated_probability": prediction.get("estimated_probability", 0.5),
            "confidence_score": prediction.get("confidence_score", 50.0),
            "expected_value": prediction.get("expected_value", 0.0),
            "fair_odd": prediction.get("fair_odd"),
            "offered_odd": prediction.get("offered_odd"),
            "risk_level": prediction.get("risk_level"),
            "home_context": explanation.get("home_context", {}),
            "away_context": explanation.get("away_context", {}),
            "league_baseline": explanation.get("league_baseline", {}),
            "poisson_prediction": {
                "estimated_probability": prediction.get("estimated_probability", 0.5),
                "confidence_score": prediction.get("confidence_score", 50.0),
            },
            "data_quality": min(
                100.0,
                45.0
                + (float((explanation.get("home_context") or {}).get("sample_size", 0)) * 4)
                + (float((explanation.get("away_context") or {}).get("sample_size", 0)) * 4),
            ),
            "recent_roi": float((self.football_skill.evaluation.current_snapshot().get("performance") or {}).get("overall_roi", 0.0) or 0.0),
        }
        context["patterns"] = self.repository.list_pattern_insights(limit=5)
        return {"prediction": prediction, "context": context}

    def analyze_football_event(self, event_id: int, *, market: str = "match_winner_home", offered_odd: float | None = None, user_id: int | None = None) -> dict[str, Any]:
        built = self._build_context(event_id, market, offered_odd)
        prediction = built["prediction"]
        context = built["context"]
        model_outputs = [
            self.time_series_model.predict(context, market),
            self.momentum_model.predict(context, market),
            self.mean_reversion_model.predict(context, market),
            self.market_efficiency_model.predict(context, market, offered_odd),
        ]
        ensemble = self.ensemble.combine(model_outputs)
        meta = self.meta.select_model(
            sport_or_market="football",
            league=str(context.get("league") or "global"),
            market=market,
            data_quality=float(context["data_quality"]),
            model_outputs=model_outputs,
        )
        drift = self.drift.detect(
            recent_roi=float(context.get("recent_roi", 0.0)),
            recent_hit_rate=float((self.football_skill.evaluation.current_snapshot().get("performance") or {}).get("overall_hit_rate", 0.0) or 0.0),
            baseline_roi=2.0,
            baseline_hit_rate=55.0,
        )
        self.repository.save_drift_event({"drift_type": drift["drift_type"], "scope": f"football:{market}", "severity": drift["severity"], **drift}, user_id=user_id)
        anomaly = self.anomaly.detect(offered_odd=prediction.get("offered_odd"), fair_odd=prediction.get("fair_odd"))
        regime = self.regime.classify(
            volatility=float(anomaly.get("score", 0.0)),
            recent_over_rate=float((context["home_context"].get("over_25_rate", 0.0) + context["away_context"].get("over_25_rate", 0.0)) / 2),
            recent_btts_rate=float((context["home_context"].get("btts_rate", 0.0) + context["away_context"].get("btts_rate", 0.0)) / 2),
        )
        risk = self.risk.evaluate(
            bankroll=1000.0,
            stake=float((prediction.get("bankroll") or {}).get("suggested_stake", 0.0) or 0.0),
            expected_value=float(prediction.get("expected_value", 0.0) or 0.0),
            confidence_score=float(prediction.get("confidence_score", 0.0) or 0.0),
            drift_score=float(drift.get("score", 0.0)),
        )
        self.repository.save_risk_event({"risk_type": "event_analysis", "severity": "high" if risk["risk_score"] >= 65 else "medium", **risk}, user_id=user_id)
        self.repository.save_exposure_snapshot({"sport_or_market": "football", **risk}, user_id=user_id)
        agent_context = {
            **context,
            "estimated_probability": ensemble["estimated_probability"],
            "confidence_score": ensemble["confidence_score"],
            "expected_value": prediction.get("expected_value", 0.0),
            "risk_score": risk["risk_score"],
            "drift_score": drift["score"],
            "consensus_hint": prediction.get("recommendation", "NO_BET"),
        }
        outputs = []
        for agent in self.agents:
            output = agent.evaluate(agent_context)
            output["event_id"] = str(event_id)
            output["market"] = market
            outputs.append(output)
            self.repository.save_agent_output(output, user_id=user_id)
            self.repository.upsert_agent_trust(agent.name, agent.context_type, float(output["trust_score"]), user_id=user_id)
        consensus = self.consensus.decide(outputs)
        self.repository.save_consensus_decision(
            {"event_id": str(event_id), "market": market, "selection": prediction.get("market"), **consensus},
            user_id=user_id,
        )
        alerts = self.alerts.build_alerts(drift=drift, risk=risk, anomaly=anomaly)
        explanation = self.explainability.explain_prediction(
            prediction=prediction,
            consensus=consensus,
            meta=meta,
            alerts=alerts,
        )
        self.memory.remember(
            "analysis",
            f"Football analysis {event_id} {market}",
            f"Decisão {consensus['final_decision']} | EV {prediction.get('expected_value')} | confiança {prediction.get('confidence_score')}",
            payload={"prediction": prediction, "consensus": consensus, "meta": meta},
            user_id=user_id,
        )
        self.repository.save_pattern_insight(
            {
                "insight_type": "event_summary",
                "label": f"{market}::{consensus['final_decision']}",
                "event_id": str(event_id),
                "expected_value": prediction.get("expected_value"),
                "confidence_score": prediction.get("confidence_score"),
            },
            user_id=user_id,
        )
        return {
            "prediction": prediction,
            "ensemble": ensemble,
            "meta": meta,
            "drift": drift,
            "regime": regime,
            "anomaly": anomaly,
            "risk": risk,
            "agent_outputs": outputs,
            "consensus": consensus,
            "explanation": explanation,
        }

    def control_center_snapshot(self, *, user_id: int | None = None) -> dict[str, Any]:
        try:
            sources = self.data_sources.list_sources()
        except Exception as exc:
            sources = []
            logger.warning("global ai data source registry failed: %s", exc)
        try:
            features = self.features.generate_for_recent_matches(limit=24, user_id=user_id)
        except Exception as exc:
            features = []
            logger.warning("global ai feature generation failed: %s", exc)
        try:
            learning = self.football_skill.continuous_learning.evaluate_and_suggest(user_id=user_id)
        except Exception as exc:
            learning = {"pending_suggestions": [], "status": "degraded", "error": str(exc)}
            logger.warning("global ai continuous learning snapshot failed: %s", exc)
        try:
            governance = self.governance.snapshot()
        except Exception as exc:
            governance = {"pending": [], "error": str(exc)}
            logger.warning("global ai governance snapshot failed: %s", exc)
        try:
            drift_events = self.repository.list_drift_events(limit=6)
        except Exception as exc:
            drift_events = []
            logger.warning("global ai drift list failed: %s", exc)
        try:
            risk_events = self.repository.list_risk_events(limit=6)
        except Exception as exc:
            risk_events = []
            logger.warning("global ai risk list failed: %s", exc)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources,
            "live_sources": self._live_source_runtime_snapshot(),
            "research_health": self._research_health_snapshot(),
            "global_snapshot": self.repository.snapshot(),
            "generated_features": features[:8],
            "learning": learning,
            "governance": governance,
            "drift_events": drift_events,
            "risk_events": risk_events,
        }

    def research_health_snapshot(self) -> dict[str, Any]:
        return self._research_health_snapshot()

    def live_source_runtime_snapshot(self) -> list[dict[str, Any]]:
        return self._live_source_runtime_snapshot()

    def football_analysis_board(self, *, limit: int = 20, market: str | None = None, user_id: int | None = None) -> dict[str, Any]:
        matches = self.football.getHistoricalEvents(limit=limit)
        rows: list[dict[str, Any]] = []
        chosen_market = market or self.settings.default_market
        for match in matches[: min(limit, 8)]:
            analysis = self.analyze_football_event(int(match["id"]), market=chosen_market, user_id=user_id)
            rows.append(
                {
                    "match": match,
                    "prediction": analysis["prediction"],
                    "consensus": analysis["consensus"],
                    "risk": analysis["risk"],
                    "meta": analysis["meta"],
                }
            )
        return {
            "market": chosen_market,
            "items": rows,
            "live_sources": self._live_source_runtime_snapshot(),
            "research_health": self._research_health_snapshot(),
        }

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        from services.footballQuantAiSkill.schemas import BacktestRequest

        summary = self.football_skill.backtesting.runBacktest(
            BacktestRequest(
                league=payload.get("league"),
                season=payload.get("season"),
                market=payload.get("market", self.settings.default_market),
                ev_min=float(payload.get("ev_min", self.settings.min_ev)),
                confidence_min=float(payload.get("confidence_min", self.settings.min_confidence)),
                date_from=payload.get("date_from"),
                date_to=payload.get("date_to"),
                bankroll=float(payload.get("bankroll", 1000.0)),
                bankroll_profile=payload.get("bankroll_profile", "moderado"),
                model_version=payload.get("model_version", "baseline"),
                user_id=payload.get("user_id"),
            )
        )
        return asdict(summary)

    def run_monte_carlo(self, *, hit_rate: float, average_odd: float, bankroll: float, stake_pct: float, user_id: int | None = None) -> dict[str, Any]:
        run = self.monte_carlo.run(
            hit_rate=hit_rate,
            average_odd=average_odd,
            bankroll=bankroll,
            stake_pct=stake_pct,
            paths=self.settings.monte_carlo_paths,
            steps=self.settings.monte_carlo_steps,
        )
        run_id = self.repository.save_monte_carlo_run(
            {
                "label": "Monte Carlo bankroll paths",
                "sport_or_market": "football",
                "market": self.settings.default_market,
                "paths": run["paths"],
                "steps": run["steps"],
                "initial_bankroll": bankroll,
                "ruin_risk": run["ruin_risk"],
                "median_final_bankroll": run["median_final_bankroll"],
                "p10_final_bankroll": run["p10_final_bankroll"],
                "p90_final_bankroll": run["p90_final_bankroll"],
            },
            user_id=user_id,
        )
        self.repository.save_monte_carlo_results(run_id, run["results"], user_id=user_id)
        return {"run_id": run_id, **run}

    def evolve_strategy(self, *, base_genome: dict[str, Any] | None = None, user_id: int | None = None) -> dict[str, Any]:
        base = base_genome or {"ev_min": self.settings.min_ev, "confidence_min": self.settings.min_confidence}
        result = self.evolution.evolve(base_genome=base, user_id=user_id)
        best = result.get("best") or {}
        version_id = self.repository.save_strategy_version(
            {
                "name": "Adaptive Strategy Draft",
                "version": f"gen-{best.get('generation', 1)}",
                "sport_or_market": "football",
                "market": self.settings.default_market,
                "rules": best.get("genome", {}),
                "status": "draft",
            },
            user_id=user_id,
        )
        request_id = self.governance.request_change(
            change_type="strategy_activation",
            target_ref=f"strategy_version:{version_id}",
            payload={"strategy_version_id": version_id, "best_candidate": best},
            user_id=user_id,
        )
        return {"strategy_version_id": version_id, "approval_request_id": request_id, **result}

    def agent_arena(self, *, prompt: str | None = None, user_id: int | None = None) -> dict[str, Any]:
        prompt = (prompt or "qual mercado performou melhor?").strip()
        agent_answer = self.football_skill.agent.answer(prompt, user_id=user_id)
        return {
            "agent_outputs": self.repository.list_consensus_decisions(limit=10),
            "trust_scores": self.repository.list_agent_trust_scores(),
            "research_agent_answer": agent_answer,
        }

    def rag_explorer(self, question: str) -> dict[str, Any]:
        return self.rag.answerWithContext(question)
