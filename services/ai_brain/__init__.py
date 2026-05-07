from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_ai_brain_metrics_service():
    from .brain_metrics_service import BrainMetricsService

    return BrainMetricsService()

