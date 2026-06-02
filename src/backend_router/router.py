"""Cost-aware backend router.

Selects the best available backend for a given SkillCall using the
scoring function:  b* = argmin_b ( λ1*cost_b + λ2*(1-reliability_b) + λ3*latency_b )

λ values are loaded from config/default.yaml and can be overridden per task.
The router respects the allowed_backends and preferred_backends fields from
the SkillTuple and the backend availability reported by each executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.contracts.types import SkillCall, SkillTuple

_COST_DEFAULTS: dict[str, float] = {
    "dom": 0.1,
    "visual": 0.5,
    "wot": 0.05,
}

_LATENCY_NORM = 2000.0


@dataclass
class RoutingDecision:
    selected_backend: str
    candidate_backends: list[str]
    routing_reason: str
    confidence_score: float


class BackendRouter:
    """Score and rank backends for a SkillCall."""

    def __init__(
        self,
        tracker: BackendConfidenceTracker | None = None,
        lambda_cost: float = 0.4,
        lambda_reliability: float = 0.4,
        lambda_latency: float = 0.2,
    ) -> None:
        self._tracker = tracker or BackendConfidenceTracker()
        self._lc = lambda_cost
        self._lr = lambda_reliability
        self._ll = lambda_latency

    def route(
        self,
        skill_call: SkillCall,
        skill_tuple: SkillTuple,
        available: list[str],
        exclude: list[str] | None = None,
    ) -> RoutingDecision:
        """Return the best backend for *skill_call* given *available* backends.

        *exclude* lists backends already tried in the current recovery cascade.
        """
        exclude = exclude or []
        candidates = [
            b
            for b in skill_tuple.allowed_backends
            if b in available and b not in exclude
        ]

        if not candidates:
            return RoutingDecision(
                selected_backend="",
                candidate_backends=[],
                routing_reason="no available backend satisfies skill constraints",
                confidence_score=0.0,
            )

        scores: dict[str, float] = {}
        for backend in candidates:
            stats = self._tracker.get_stats(backend)
            cost = _COST_DEFAULTS.get(backend, 1.0)
            reliability_penalty = 1.0 - stats.reliability
            latency_norm = min(stats.latency / _LATENCY_NORM, 1.0)
            score = self._lc * cost + self._lr * reliability_penalty + self._ll * latency_norm
            scores[backend] = score

        best = min(scores, key=lambda b: scores[b])
        confidence = 1.0 - scores[best]

        reason = (
            f"scored {best}={scores[best]:.3f} among {list(scores.keys())}"
        )
        if best in skill_tuple.preferred_backends:
            reason += " (preferred)"

        return RoutingDecision(
            selected_backend=best,
            candidate_backends=candidates,
            routing_reason=reason,
            confidence_score=max(0.0, confidence),
        )

    def record_outcome(self, backend: str, success: bool, latency_ms: float) -> None:
        self._tracker.record(backend, success, latency_ms)
