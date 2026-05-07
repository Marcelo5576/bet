from __future__ import annotations

from functools import lru_cache
from typing import Any


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

    def health(self) -> dict[str, Any]:
        snapshot = self.repository.system_snapshot()
        return {
            "ok": True,
            "db_file": snapshot["db_file"],
            "counts": snapshot["counts"],
            "sources": self.data_sources.source_status(),
            "supabase": self.supabase.sync_status(),
        }


@lru_cache(maxsize=1)
def get_football_quant_ai_skill() -> FootballQuantAiSkill:
    return FootballQuantAiSkill()
