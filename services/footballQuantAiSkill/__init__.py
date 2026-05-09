from __future__ import annotations

from functools import lru_cache
import logging
from typing import Any


logger = logging.getLogger("football_quant.health")


class FootballQuantAiSkill:
    def __init__(self):
        from src.config import load_settings

        from .agents.football_research_agent import FootballResearchAgent
        from .backtesting.backtesting_service import BacktestingService
        from .bankroll.bankroll_service import BankrollService
        from .config import load_research_skill_settings
        from .continuous_learning.continuous_learning_service import ContinuousLearningService
        from .continuous_learning.learning_memory_service import LearningMemoryService
        from .continuous_learning.model_evaluation_service import ModelEvaluationService
        from .continuous_learning.recommendation_refinement_service import RecommendationRefinementService
        from .continuous_learning.strategy_rule_service import StrategyRuleService
        from .data_source_service import DataSourceService
        from .evaluation.database_discovery_service import DatabaseDiscoveryService
        from .feature_engineering.historical_feature_store import HistoricalFeatureStore
        from .historical_data_service import HistoricalDataService
        from .models.hybrid_prediction_service import HybridPredictionService
        from .models.poisson_model_service import PoissonModelService
        from .rag.rag_knowledge_service import RagKnowledgeService
        from .repository import FootballResearchRepository
        from .statistics.football_stats_service import FootballStatsService
        from .supabase.research_sync_service import ResearchSupabaseSyncService
        from .value_betting.value_bet_service import ValueBetService

        self.settings = load_research_skill_settings()
        base_settings = load_settings()
        self.repository = FootballResearchRepository(self.settings.db_file)
        self.data_sources = DataSourceService(self.settings, self.repository)
        self.stats = FootballStatsService(self.repository)
        self.poisson = PoissonModelService()
        self.value_betting = ValueBetService()
        self.bankroll = BankrollService()
        self.prediction = HybridPredictionService(
            self.repository,
            self.stats,
            self.poisson,
            self.value_betting,
            self.bankroll,
            default_bankroll=self.settings.default_bankroll,
            default_profile=self.settings.default_profile,
            min_ev_to_recommend=self.settings.min_ev_to_recommend,
            min_confidence_to_recommend=self.settings.min_confidence_to_recommend,
        )
        self.historical = HistoricalDataService(self.repository, self.data_sources)
        self.feature_store = HistoricalFeatureStore(self.repository)
        self.backtesting = BacktestingService(self.repository, self.prediction)
        self.learning_memory = LearningMemoryService(self.repository)
        self.strategy_rules = StrategyRuleService(self.repository)
        self.recommendations = RecommendationRefinementService(self.repository)
        self.evaluation = ModelEvaluationService(self.repository)
        self.continuous_learning = ContinuousLearningService(
            self.learning_memory,
            self.strategy_rules,
            self.recommendations,
            self.evaluation,
        )
        self.rag = RagKnowledgeService(self.repository)
        self.agent = FootballResearchAgent(self.repository, self.rag, self.evaluation, self.backtesting)
        self.discovery = DatabaseDiscoveryService(
            self.repository,
            portal_db_file=base_settings.portal_db_file,
            state_file=base_settings.state_file,
            brain_db_file=base_settings.brain_db_file,
            supabase_url=self.settings.supabase_url,
            supabase_service_role_key=self.settings.supabase_service_role_key,
        )
        self.supabase = ResearchSupabaseSyncService(
            self.repository,
            supabase_url=self.settings.supabase_url,
            supabase_service_role_key=self.settings.supabase_service_role_key,
        )
        if self.settings.auto_seed_mocks:
            self._bootstrap()

    def _bootstrap(self) -> None:
        from .data_sources.mock_source import MockFootballDataSource
        from .normalization.normalizer import FootballDataNormalizer

        snapshot = self.repository.system_snapshot()
        if snapshot["counts"].get("historical_matches", 0) > 0:
            return
        normalizer = FootballDataNormalizer()
        mock_rows = MockFootballDataSource()._matches()
        normalized = [normalizer.normalize_match(item, source="Mock Local") for item in mock_rows]
        self.repository.import_normalized_matches(normalized, source_name="Mock Local")
        self.rag.ingestDocument(
            "Aviso legal",
            "Este sistema é apenas uma ferramenta estatística de apoio. Não garante lucro. Aposte com responsabilidade.",
            source_type="policy",
            source_ref="legal_notice",
            metadata={"category": "compliance"},
        )

    def _safe_system_snapshot(self) -> dict[str, Any]:
        try:
            snapshot = self.repository.system_snapshot()
        except Exception as exc:
            logger.warning("football_quant health snapshot failed: %s", exc)
            return {"db_file": self.settings.db_file, "counts": {}}
        if not isinstance(snapshot, dict):
            return {"db_file": self.settings.db_file, "counts": {}}
        counts = snapshot.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        return {
            "db_file": str(snapshot.get("db_file") or self.settings.db_file),
            "counts": counts,
        }

    def _safe_source_status(self) -> list[dict[str, Any]]:
        try:
            rows = self.data_sources.source_status()
        except Exception as exc:
            logger.warning("football_quant source status failed: %s", exc)
            return []
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    def _safe_supabase_status(self) -> dict[str, Any]:
        try:
            status = self.supabase.sync_status()
        except Exception as exc:
            logger.warning("football_quant supabase status failed: %s", exc)
            return {
                "enabled": bool(self.settings.supabase_url and self.settings.supabase_service_role_key),
                "supabase_url": self.settings.supabase_url or "",
                "local_snapshot": self._safe_system_snapshot(),
                "last_hydrate_at": None,
                "last_hydrate_result": {},
                "last_error": str(exc),
                "schema_mode": "unavailable",
                "available_tables": {},
                "table_probe_errors": {},
                "last_capability_check_at": None,
                "note": "Falha ao consultar o status remoto; seguimos com o cache local.",
            }
        if not isinstance(status, dict):
            return {
                "enabled": False,
                "supabase_url": self.settings.supabase_url or "",
                "local_snapshot": self._safe_system_snapshot(),
                "last_hydrate_at": None,
                "last_hydrate_result": {},
                "last_error": f"status_invalido:{type(status).__name__}",
                "schema_mode": "unavailable",
                "available_tables": {},
                "table_probe_errors": {},
                "last_capability_check_at": None,
                "note": "O retorno do sincronismo remoto veio em formato invalido; seguimos com o cache local.",
            }
        status.setdefault("enabled", bool(self.settings.supabase_url and self.settings.supabase_service_role_key))
        status.setdefault("supabase_url", self.settings.supabase_url or "")
        if not isinstance(status.get("local_snapshot"), dict):
            status["local_snapshot"] = self._safe_system_snapshot()
        if not isinstance(status.get("last_hydrate_result"), dict):
            status["last_hydrate_result"] = {}
        if not isinstance(status.get("available_tables"), dict):
            status["available_tables"] = {}
        if not isinstance(status.get("table_probe_errors"), dict):
            status["table_probe_errors"] = {}
        return status

    def health(self) -> dict[str, Any]:
        snapshot = self._safe_system_snapshot()
        counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
        local_matches = int(counts.get("historical_matches") or 0) if isinstance(counts, dict) else 0
        local_features = int(counts.get("historical_features") or 0) if isinstance(counts, dict) else 0
        if bool(self.settings.supabase_url and self.settings.supabase_service_role_key) and (local_matches == 0 or local_features == 0):
            try:
                self.supabase.hydrate_local_cache_if_needed(
                    min_local_matches=300,
                    min_local_features=300,
                    recent_match_limit=400,
                    recent_feature_limit=600,
                )
                snapshot = self._safe_system_snapshot()
            except Exception as exc:
                logger.warning("football_quant initial hydrate failed: %s", exc)
        return {
            "ok": True,
            "db_file": snapshot.get("db_file") or self.settings.db_file,
            "counts": snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {},
            "sources": self._safe_source_status(),
            "supabase": self._safe_supabase_status(),
        }


@lru_cache(maxsize=1)
def get_football_quant_ai_skill() -> FootballQuantAiSkill:
    return FootballQuantAiSkill()
