"""Runtime rate limiter for safety checks."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_s: float = 0.1) -> None:
        self.min_interval_s = min_interval_s
        self._last_seen: dict[str, float] = {}

    def allow(self, key: str, operation: str | None = None) -> bool:
        scoped_key = _scope_key(key, operation)
        now = time.monotonic()
        last_seen = self._last_seen.get(scoped_key)
        if last_seen is not None and now - last_seen < self.min_interval_s:
            return False
        self._last_seen[scoped_key] = now
        return True

    def remaining_wait_s(self, key: str, operation: str | None = None) -> float:
        scoped_key = _scope_key(key, operation)
        last_seen = self._last_seen.get(scoped_key)
        if last_seen is None:
            return 0.0
        elapsed = time.monotonic() - last_seen
        return max(0.0, self.min_interval_s - elapsed)


def _scope_key(key: str, operation: str | None = None) -> str:
    if not key.strip():
        raise ValueError("rate-limit key must be non-empty")
    if operation is None:
        return key
    if not operation.strip():
        raise ValueError("rate-limit operation must be non-empty")
    return f"{key}:{operation}"
