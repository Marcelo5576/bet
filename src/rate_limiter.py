from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import threading
import time


def mask_secret(secret: str | None) -> str:
    raw = str(secret or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return raw[:2] + "*" * max(0, len(raw) - 2)
    return raw[:6] + "*" * max(4, len(raw) - 10) + raw[-4:]


def sanitize_text(text: str | None, *secrets: str | None) -> str:
    cleaned = str(text or "")
    for secret in secrets:
        raw = str(secret or "").strip()
        if raw:
            cleaned = cleaned.replace(raw, mask_secret(raw))
    cleaned = re.sub(
        r"((?:api[_-]?key|key|token)=)([^&\\s]+)",
        lambda m: m.group(1) + mask_secret(m.group(2)),
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(Bearer\\s+)([A-Za-z0-9._\\-]+)",
        lambda m: m.group(1) + mask_secret(m.group(2)),
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned[:320]


def retry_after_seconds(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(float(raw)))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        wait = int((parsed - datetime.now(timezone.utc)).total_seconds())
        return max(0, wait)
    except Exception:
        return None


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    wait_seconds: int = 0
    reason: str = ""
    cooling_down: bool = False


@dataclass
class _ProviderState:
    timestamps: deque[float] = field(default_factory=deque)
    cooldown_until: float = 0.0
    last_reason: str = ""


class ProviderRateLimiter:
    def __init__(self) -> None:
        self._states: dict[str, _ProviderState] = {}
        self._lock = threading.Lock()

    def acquire(self, provider: str, max_calls_per_minute: int) -> LimitDecision:
        name = str(provider or "").strip().lower() or "provider"
        max_calls = max(1, int(max_calls_per_minute or 1))
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(name, _ProviderState())
            self._prune(state, now)
            if state.cooldown_until > now:
                wait_seconds = max(1, int(state.cooldown_until - now))
                return LimitDecision(
                    allowed=False,
                    wait_seconds=wait_seconds,
                    reason=state.last_reason or "cooldown ativo",
                    cooling_down=True,
                )
            if len(state.timestamps) >= max_calls:
                wait_seconds = max(1, int(60 - (now - state.timestamps[0])))
                return LimitDecision(
                    allowed=False,
                    wait_seconds=wait_seconds,
                    reason=f"rate limit local {len(state.timestamps)}/{max_calls} rpm",
                )
            state.timestamps.append(now)
            return LimitDecision(allowed=True)

    def cooldown(self, provider: str, seconds: int, *, reason: str = "") -> None:
        name = str(provider or "").strip().lower() or "provider"
        wait_seconds = max(1, int(seconds or 1))
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(name, _ProviderState())
            state.cooldown_until = max(state.cooldown_until, now + wait_seconds)
            state.last_reason = str(reason or state.last_reason or "cooldown ativo").strip()

    def status(self, provider: str, max_calls_per_minute: int) -> LimitDecision:
        name = str(provider or "").strip().lower() or "provider"
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(name, _ProviderState())
            self._prune(state, now)
            if state.cooldown_until > now:
                wait_seconds = max(1, int(state.cooldown_until - now))
                return LimitDecision(
                    allowed=False,
                    wait_seconds=wait_seconds,
                    reason=state.last_reason or "cooldown ativo",
                    cooling_down=True,
                )
            max_calls = max(1, int(max_calls_per_minute or 1))
            if len(state.timestamps) >= max_calls:
                wait_seconds = max(1, int(60 - (now - state.timestamps[0])))
                return LimitDecision(
                    allowed=False,
                    wait_seconds=wait_seconds,
                    reason=f"rate limit local {len(state.timestamps)}/{max_calls} rpm",
                )
            return LimitDecision(allowed=True)

    def _prune(self, state: _ProviderState, now: float) -> None:
        while state.timestamps and now - state.timestamps[0] >= 60:
            state.timestamps.popleft()


_DEFAULT_LIMITER = ProviderRateLimiter()


def get_provider_limiter() -> ProviderRateLimiter:
    return _DEFAULT_LIMITER

