"""Runtime rate limiter for safety checks."""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, min_interval_s: float = 0.1) -> None:
        self.min_interval_s = min_interval_s
        self._last_seen: dict[str, float] = defaultdict(float)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if now - self._last_seen[key] < self.min_interval_s:
            return False
        self._last_seen[key] = now
        return True
