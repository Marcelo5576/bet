from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def get_global_adaptive_intelligence():
    from .core import GlobalAdaptiveIntelligencePlatform

    return GlobalAdaptiveIntelligencePlatform()
