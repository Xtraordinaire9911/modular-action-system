"""Cost-aware backend router (advisor §1.4, slide 8 scoring function).

Implements ``b* = argmin_b  λ1·c_b + λ2·(1 − r_b) + λ3·ℓ_b`` over the backends
that (a) are allowed by the skill contract, (b) are enabled by the current
evaluation mode (full / DOM-only / WoT-only / VAM-only / no-recovery), and
(c) actually have a grounded affordance available this step.

  * c_b  — invocation cost proxy (WoT cheap & verifiable, DOM medium, Visual/VAM
           expensive: it wakes the heavy System-2 supervisor).
  * r_b  — learned reliability from :class:`BackendConfidenceTracker`.
  * ℓ_b  — normalised mean latency.

Preferred backends from the skill contract get a small score bonus so ties break
toward the planner's intent. The router never picks visual when a high-reliability
deterministic backend is available — keeping the System-1-first principle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.contracts.types import Affordance, SkillTuple

# Base invocation-cost proxy per backend (0 = free, 1 = most expensive).
DEFAULT_COST = {"wot": 0.1, "dom": 0.3, "visual": 1.0}
# Backends enabled per evaluation variant.
MODE_BACKENDS = {
    "full": {"dom", "wot", "visual"},
    "no-recovery": {"dom", "wot", "visual"},
    "dom-only": {"dom"},
    "wot-only": {"wot"},
    "vam-only": {"visual"},
    "visual-only": {"visual"},
}
_LATENCY_NORM_MS = 2000.0  # latency normaliser → ℓ in [0, ~1]
_AFF_SOURCE_TO_BACKEND = {"DOM": "dom", "WOT": "wot", "VISUAL": "visual"}


@dataclass
class RoutingDecision:
    selected_backend: str | None
    candidate_backends: list[str]
    routing_reason: str
    score: float
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)


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
        mode_set = MODE_BACKENDS.get(self._mode, MODE_BACKENDS["full"])
        return mode_set & set(skill.allowed_backends)

    def _score(self, backend: str, preferred: set[str]) -> float:
        c = self._cost.get(backend, 0.5)
        r = self._tracker.reliability(backend)
        latency = min(self._tracker.mean_latency(backend) / _LATENCY_NORM_MS, 1.0)
        score = self._l1 * c + self._l2 * (1.0 - r) + self._l3 * latency
        if backend in preferred:
            score -= 0.05  # gentle bias toward the planner's preference
        return score

    def route(self, skill: SkillTuple, candidates: dict[str, Affordance]) -> RoutingDecision:
        """``candidates`` maps backend name → best grounded affordance this step."""
        available = {_AFF_SOURCE_TO_BACKEND.get(a.source, b): a for b, a in candidates.items()}
        enabled = self._enabled(skill)
        viable = [b for b in available if b in enabled]
        if not viable:
            return RoutingDecision(None, [], f"no enabled backend (mode={self._mode})", float("inf"), 0.0)

        preferred = set(skill.preferred_backends)
        scores = {b: self._score(b, preferred) for b in viable}
        best = min(scores, key=lambda b: scores[b])
        return RoutingDecision(
            selected_backend=best,
            candidate_backends=sorted(viable, key=lambda b: scores[b]),
            routing_reason=(
                f"min-cost backend among {sorted(viable)}; "
                f"c={self._cost.get(best):.2f} r={self._tracker.reliability(best):.2f}"
            ),
            score=round(scores[best], 4),
            confidence=available[best].confidence,
            scores={b: round(s, 4) for b, s in scores.items()},
        )

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None:
        """Feed an execution outcome back so future routing reflects reality."""
        self._tracker.update(backend, success=success, latency_ms=latency_ms)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self._mode, "lambdas": [self._l1, self._l2, self._l3], "stats": self._tracker.snapshot()}
