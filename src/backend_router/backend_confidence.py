"""Per-backend reliability/latency tracker feeding the cost-aware router.

Reliability is an exponential moving average (EMA) of success, so a backend that
starts failing (e.g. DOM after a redeploy, WoT during a timeout storm) is
down-weighted by the router within a few observations and recovers as it starts
succeeding again. This is the empirical signal behind Trace-Based Skill
Evolution: backend statistics learned from execution traces.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackendStats:
    reliability: float = 0.8  # prior: optimistic but not certain
    mean_latency_ms: float = 100.0
    samples: int = 0


class BackendConfidenceTracker:
    def __init__(self, *, alpha: float = 0.3, latency_alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._latency_alpha = latency_alpha
        self._stats: dict[str, BackendStats] = {}

    def _stat(self, backend: str) -> BackendStats:
        return self._stats.setdefault(backend, BackendStats())

    def update(self, backend: str, *, success: bool, latency_ms: float) -> None:
        s = self._stat(backend)
        s.reliability = (1 - self._alpha) * s.reliability + self._alpha * (1.0 if success else 0.0)
        s.mean_latency_ms = (1 - self._latency_alpha) * s.mean_latency_ms + self._latency_alpha * latency_ms
        s.samples += 1

    def reliability(self, backend: str) -> float:
        return self._stat(backend).reliability

    def mean_latency(self, backend: str) -> float:
        return self._stat(backend).mean_latency_ms

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            b: {"reliability": round(s.reliability, 4), "mean_latency_ms": round(s.mean_latency_ms, 2), "samples": s.samples}
            for b, s in self._stats.items()
        }
