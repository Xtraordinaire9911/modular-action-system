"""Cost-aware backend routing.

Supports the B-103 affordance-candidate router and the earlier develop
skill-level BackendRouter API over one confidence tracker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.contracts.types import Affordance, SkillCall, SkillTuple

DEFAULT_COST = {"wot": 0.1, "dom": 0.3, "visual": 1.0}
_COST_DEFAULTS = {"dom": 0.1, "visual": 0.5, "wot": 0.05}
MODE_BACKENDS = {
    "full": {"dom", "wot", "visual"},
    "no-recovery": {"dom", "wot"},
    "dom-only": {"dom"},
    "wot-only": {"wot"},
    "vam-only": {"visual"},
    "visual-only": {"visual"},
}
_LATENCY_NORM_MS = 2000.0
_AFF_SOURCE_TO_BACKEND = {"DOM": "dom", "WOT": "wot", "VISUAL": "visual"}


@dataclass
class RoutingDecision:
    selected_backend: str | None
    candidate_backends: list[str]
    routing_reason: str
    score: float = 0.0
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def confidence_score(self) -> float:
        return self.confidence


class CostAwareRouter:
    def __init__(
        self,
        tracker: BackendConfidenceTracker | None = None,
        *,
        lambdas: tuple[float, float, float] = (1.0, 1.0, 1.0),
        mode: str = "full",
        cost: dict[str, float] | None = None,
    ) -> None:
        self._tracker = tracker or BackendConfidenceTracker()
        self._l1, self._l2, self._l3 = lambdas
        self._mode = mode
        self._cost = cost or dict(DEFAULT_COST)

    def _enabled(self, skill: SkillTuple) -> set[str]:
        return MODE_BACKENDS.get(self._mode, MODE_BACKENDS["full"]) & set(skill.allowed_backends)

    def _score(self, backend: str, preferred: set[str]) -> float:
        cost = self._cost.get(backend, 0.5)
        reliability = self._tracker.reliability(backend)
        latency = min(self._tracker.mean_latency(backend) / _LATENCY_NORM_MS, 1.0)
        score = self._l1 * cost + self._l2 * (1.0 - reliability) + self._l3 * latency
        if backend in preferred:
            score -= 0.05
        return score

    def route(self, skill: SkillTuple, candidates: dict[str, Affordance]) -> RoutingDecision:
        available = {_AFF_SOURCE_TO_BACKEND.get(aff.source, backend): aff for backend, aff in candidates.items()}
        viable = [backend for backend in available if backend in self._enabled(skill)]
        if not viable:
            return RoutingDecision(None, [], f"no enabled backend (mode={self._mode})", float("inf"), 0.0)

        preferred = set(skill.preferred_backends)
        scores = {backend: self._score(backend, preferred) for backend in viable}
        best = min(scores, key=lambda backend: scores[backend])
        return RoutingDecision(
            selected_backend=best,
            candidate_backends=sorted(viable, key=lambda backend: scores[backend]),
            routing_reason=(
                f"min-cost backend among {sorted(viable)}; "
                f"c={self._cost.get(best, 0.5):.2f} r={self._tracker.reliability(best):.2f}"
            ),
            score=round(scores[best], 4),
            confidence=available[best].confidence,
            scores={backend: round(score, 4) for backend, score in scores.items()},
        )

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None:
        self._tracker.update(backend, success=success, latency_ms=latency_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "lambdas": [self._l1, self._l2, self._l3],
            "stats": self._tracker.snapshot(),
        }


class BackendRouter:
    """Develop-compatible skill-level router."""

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
        exclude = exclude or []
        candidates = [
            backend
            for backend in skill_tuple.allowed_backends
            if backend in available and backend not in exclude
        ]
        if not candidates:
            return RoutingDecision(
                selected_backend="",
                candidate_backends=[],
                routing_reason="no available backend satisfies skill constraints",
                score=float("inf"),
                confidence=0.0,
            )

        weight_sum = self._lc + self._lr + self._ll
        if weight_sum <= 0:
            raise ValueError("routing weights must sum to a positive value")
        scores: dict[str, float] = {}
        for backend in candidates:
            stats = self._tracker.get_stats(backend)
            cost = _COST_DEFAULTS.get(backend, 1.0)
            reliability_penalty = 1.0 - stats.reliability
            latency_norm = min(stats.latency / _LATENCY_NORM_MS, 1.0)
            scores[backend] = (self._lc * cost + self._lr * reliability_penalty + self._ll * latency_norm) / weight_sum

        best = min(scores, key=lambda backend: scores[backend])
        reason = f"scored {best}={scores[best]:.3f} among {list(scores.keys())}"
        if best in skill_tuple.preferred_backends:
            reason += " (preferred)"
        return RoutingDecision(
            selected_backend=best,
            candidate_backends=candidates,
            routing_reason=reason,
            score=round(scores[best], 4),
            confidence=max(0.0, min(1.0, 1.0 - scores[best])),
            scores={backend: round(score, 4) for backend, score in scores.items()},
        )

    def record_outcome(self, backend: str, success: bool, latency_ms: float) -> None:
        self._tracker.record(backend, success, latency_ms)
