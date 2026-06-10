"""Runtime rate limiter for safety checks."""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, min_interval_s: float = 0.1) -> None:
        self.min_interval_s = min_interval_s
        self._last_seen: dict[str, float] = defaultdict(float)

    def allow(self, key: str, operation: str | None = None) -> bool:
        scoped_key = _scope_key(key, operation)
        now = time.monotonic()
        if now - self._last_seen[scoped_key] < self.min_interval_s:
            return False
        self._last_seen[scoped_key] = now
        return True

    def remaining_wait_s(self, key: str, operation: str | None = None) -> float:
        scoped_key = _scope_key(key, operation)
        elapsed = time.monotonic() - self._last_seen[scoped_key]
        return max(0.0, self.min_interval_s - elapsed)


def _scope_key(key: str, operation: str | None = None) -> str:
    if not key.strip():
        raise ValueError("rate-limit key must be non-empty")
    if operation is None:
        return key
    if not operation.strip():
        raise ValueError("rate-limit operation must be non-empty")
    return f"{key}:{operation}"
