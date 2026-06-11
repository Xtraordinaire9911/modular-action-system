"""Backend confidence tracking for routing decisions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackendStats:
    backend: str = ""
    reliability: float = 0.8
    mean_latency_ms: float = 100.0
    samples: int = 0
    alpha: float = 0.3
    latency_alpha: float = 0.3

    def update(self, success: bool, latency_ms: float) -> None:
        self.samples += 1
        self.reliability = (1 - self.alpha) * self.reliability + self.alpha * (1.0 if success else 0.0)
        self.mean_latency_ms = (1 - self.latency_alpha) * self.mean_latency_ms + self.latency_alpha * latency_ms

    @property
    def ema_success(self) -> float:
        return self.reliability

    @property
    def ema_latency_ms(self) -> float:
        return self.mean_latency_ms

    @property
    def latency(self) -> float:
        return self.mean_latency_ms

    @property
    def total_calls(self) -> int:
        return self.samples


class BackendConfidenceTracker:
    """EMA reliability/latency store with both B-103 and develop API names."""

    def __init__(self, *, alpha: float = 0.3, latency_alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._latency_alpha = latency_alpha
        self._stats: dict[str, BackendStats] = {}

    def _stat(self, backend: str) -> BackendStats:
        if backend not in self._stats:
            self._stats[backend] = BackendStats(
                backend=backend,
                alpha=self._alpha,
                latency_alpha=self._latency_alpha,
            )
        return self._stats[backend]

    def update(self, backend: str, *, success: bool, latency_ms: float) -> None:
        self._stat(backend).update(success, latency_ms)

    def record(self, backend: str, success: bool, latency_ms: float) -> None:
        self.update(backend, success=success, latency_ms=latency_ms)

    def reliability(self, backend: str) -> float:
        return self._stat(backend).reliability

    def mean_latency(self, backend: str) -> float:
        return self._stat(backend).mean_latency_ms

    def get_stats(self, backend: str) -> BackendStats:
        return self._stat(backend)

    def all_stats(self) -> dict[str, BackendStats]:
        return dict(self._stats)

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            backend: {
                "reliability": round(stats.reliability, 4),
                "mean_latency_ms": round(stats.mean_latency_ms, 2),
                "samples": stats.samples,
            }
            for backend, stats in self._stats.items()
        }
