"""Backend confidence scoring.

Tracks per-backend exponential moving averages of success rate and latency
so the router has a live signal to work with between episodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BackendStats:
    """Exponential moving average of success rate and mean latency for one backend."""

    backend: str
    ema_success: float = 1.0
    ema_latency_ms: float = 200.0
    total_calls: int = 0
    alpha: float = 0.3

    def update(self, success: bool, latency_ms: float) -> None:
        self.total_calls += 1
        self.ema_success = self.alpha * float(success) + (1 - self.alpha) * self.ema_success
        self.ema_latency_ms = self.alpha * latency_ms + (1 - self.alpha) * self.ema_latency_ms

    @property
    def reliability(self) -> float:
        return self.ema_success

    @property
    def latency(self) -> float:
        return self.ema_latency_ms


class BackendConfidenceTracker:
    """Aggregates BackendStats for all known backends."""

    def __init__(self) -> None:
        self._stats: dict[str, BackendStats] = {}

    def _ensure(self, backend: str) -> BackendStats:
        if backend not in self._stats:
            self._stats[backend] = BackendStats(backend=backend)
        return self._stats[backend]

    def record(self, backend: str, success: bool, latency_ms: float) -> None:
        self._ensure(backend).update(success, latency_ms)

    def get_stats(self, backend: str) -> BackendStats:
        return self._ensure(backend)

    def all_stats(self) -> dict[str, BackendStats]:
        return dict(self._stats)
