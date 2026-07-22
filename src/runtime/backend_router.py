"""Canonical backend routing over runtime affordances.

All production and compatibility routing APIs delegate to this module.  The
router owns candidate filtering and decision construction, while pluggable
scorers provide either the runtime rule-first policy or cost-aware evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.contracts.types import SkillCall
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance

DEFAULT_COST = {"wot": 0.1, "dom": 0.3, "visual": 1.0}
MODE_BACKENDS = {
    "full": {"dom", "wot", "visual", "system"},
    "no-recovery": {"dom", "wot"},
    "dom-only": {"dom"},
    "wot-only": {"wot"},
    "vam-only": {"visual"},
    "visual-only": {"visual"},
}
_LATENCY_NORM_MS = 2000.0


@dataclass
class RoutingDecision:
    backend: str
    affordance_id: str
    reason: str
    confidence: float
    candidate_backends: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    score: float = 0.0

    @property
    def selected_backend(self) -> str | None:
        """Compatibility name used by the former cost-aware router API."""

        return self.backend or None

    @property
    def routing_reason(self) -> str:
        return self.reason

    @property
    def confidence_score(self) -> float:
        return self.confidence


@dataclass
class RecoveryRoutingContext:
    exclude_backends: list[str]
    previous_failures: dict[str, int] | None = None


class RoutingScorer(Protocol):
    def score(
        self,
        affordance: RuntimeAffordance,
        *,
        skill_id: str,
        preferred_backend: str,
        previous_failures: dict[str, int] | None,
        policy_overlay: Any | None,
    ) -> float: ...

    def explain(self, backend: str) -> str: ...

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...


class RuleFirstRoutingScorer:
    """Default runtime scoring policy, preserving the original CIM behavior."""

    def score(
        self,
        affordance: RuntimeAffordance,
        *,
        skill_id: str,
        preferred_backend: str,
        previous_failures: dict[str, int] | None,
        policy_overlay: Any | None,
    ) -> float:
        source_bias = {"wot": 0.05, "dom": 0.03, "visual": 0.0, "system": -0.1}.get(affordance.source, 0.0)
        preferred_bonus = 0.1 if affordance.source == preferred_backend else 0.0
        failure_penalty = 0.15 * (previous_failures or {}).get(affordance.source, 0)
        learned_penalty = (
            policy_overlay.skill_backend_penalty(skill_id, affordance.source) if policy_overlay is not None else 0.0
        )
        return affordance.confidence + source_bias + preferred_bonus - failure_penalty - learned_penalty

    def explain(self, backend: str) -> str:
        return f"rule-first score selected {backend}"

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None:
        _ = (backend, success, latency_ms)

    def snapshot(self) -> dict[str, Any]:
        return {"strategy": "rule_first"}


class CostAwareRoutingScorer:
    """Cost/reliability/latency scorer shared by runtime and evaluation APIs."""

    def __init__(
        self,
        tracker: BackendConfidenceTracker | None = None,
        *,
        lambdas: tuple[float, float, float] = (1.0, 1.0, 1.0),
        cost: dict[str, float] | None = None,
    ) -> None:
        self.tracker = tracker or BackendConfidenceTracker()
        self.lambda_cost, self.lambda_reliability, self.lambda_latency = lambdas
        if self.lambda_cost + self.lambda_reliability + self.lambda_latency <= 0:
            raise ValueError("routing weights must sum to a positive value")
        self.cost = dict(cost or DEFAULT_COST)

    def score(
        self,
        affordance: RuntimeAffordance,
        *,
        skill_id: str,
        preferred_backend: str,
        previous_failures: dict[str, int] | None,
        policy_overlay: Any | None,
    ) -> float:
        backend = affordance.source
        penalty = (
            self.lambda_cost * self.cost.get(backend, 0.5)
            + self.lambda_reliability * (1.0 - self.tracker.reliability(backend))
            + self.lambda_latency * min(self.tracker.mean_latency(backend) / _LATENCY_NORM_MS, 1.0)
        )
        if backend == preferred_backend:
            penalty -= 0.05
        penalty += 0.15 * (previous_failures or {}).get(backend, 0)
        if policy_overlay is not None:
            penalty += policy_overlay.skill_backend_penalty(skill_id, backend)
        return -penalty

    def explain(self, backend: str) -> str:
        return (
            f"cost-aware score selected {backend}; "
            f"cost={self.cost.get(backend, 0.5):.2f}, "
            f"reliability={self.tracker.reliability(backend):.2f}, "
            f"latency_ms={self.tracker.mean_latency(backend):.1f}"
        )

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None:
        self.tracker.update(backend, success=success, latency_ms=latency_ms)

    def snapshot(self) -> dict[str, Any]:
        return {
            "strategy": "cost_aware",
            "lambdas": [self.lambda_cost, self.lambda_reliability, self.lambda_latency],
            "stats": self.tracker.snapshot(),
        }


class RuntimeBackendRouter:
    """Single routing core used by CIM and legacy/evaluation adapters."""

    def __init__(
        self,
        policy_overlay: Any | None = None,
        *,
        scorer: RoutingScorer | None = None,
        mode: str = "full",
    ) -> None:
        if mode not in MODE_BACKENDS:
            raise ValueError(f"unknown routing mode: {mode}")
        self._overlay = policy_overlay
        self._scorer = scorer or RuleFirstRoutingScorer()
        self._mode = mode

    def _preferred_backend(self, skill_call: SkillCall) -> str:
        if self._overlay is not None:
            preferred_backend = getattr(self._overlay, "preferred_backend", None)
            if callable(preferred_backend):
                override = preferred_backend(skill_call.skill_id)
                if override:
                    return override
        if skill_call.preferred_backends:
            return skill_call.preferred_backends[0]
        return _default_preferred_backend(skill_call.skill_id)

    def select_backend(
        self,
        skill_call: SkillCall,
        cognitive_map: CognitiveMap,
        recovery_context: RecoveryRoutingContext | None = None,
    ) -> RoutingDecision:
        excluded = set(recovery_context.exclude_backends if recovery_context else [])
        previous_failures = recovery_context.previous_failures if recovery_context else None
        enabled = MODE_BACKENDS[self._mode]
        all_affordances = cognitive_map.get_affordances_for_skill(skill_call.skill_id)
        candidates = [
            affordance
            for affordance in all_affordances
            if affordance.source not in excluded and affordance.source in enabled
        ]
        if not candidates:
            if all_affordances and excluded:
                reason = "all candidate backends excluded by recovery context"
            elif all_affordances:
                reason = f"no candidate backend enabled in mode={self._mode}"
            else:
                reason = "no affordance available"
            return RoutingDecision("", "", reason, 0.0, score=float("inf"))

        preferred = self._preferred_backend(skill_call)
        scored = {
            affordance.id: self._scorer.score(
                affordance,
                skill_id=skill_call.skill_id,
                preferred_backend=preferred,
                previous_failures=previous_failures,
                policy_overlay=self._overlay,
            )
            for affordance in candidates
        }
        ranked = sorted(candidates, key=lambda affordance: scored[affordance.id], reverse=True)
        best = ranked[0]
        if isinstance(self._scorer, RuleFirstRoutingScorer):
            reason = f"selected {best.source} for {skill_call.skill_id}"
        else:
            reason = self._scorer.explain(best.source)
        if best.source != preferred:
            reason += f"; preferred backend {preferred} was unavailable or scored lower"
        if previous_failures and previous_failures.get(best.source, 0) > 0:
            reason += "; selected despite prior failures because no stronger candidate exists"
        backend_scores: dict[str, float] = {}
        for affordance in ranked:
            backend_scores.setdefault(affordance.source, round(scored[affordance.id], 4))
        return RoutingDecision(
            backend=best.source,
            affordance_id=best.id,
            reason=reason,
            confidence=best.confidence,
            candidate_backends=list(backend_scores),
            scores=backend_scores,
            score=(
                round(-scored[best.id], 4)
                if isinstance(self._scorer, CostAwareRoutingScorer)
                else round(scored[best.id], 4)
            ),
        )

    def record_outcome(self, backend: str, *, success: bool, latency_ms: float) -> None:
        self._scorer.observe(backend, success=success, latency_ms=latency_ms)

    def snapshot(self) -> dict[str, Any]:
        return {"mode": self._mode, **self._scorer.snapshot()}


def _default_preferred_backend(skill_id: str) -> str:
    if skill_id in {"set_temperature", "set_lighting", "turn_on_projector", "verify_readiness"}:
        return "wot"
    if skill_id == "confirm_booking":
        return "dom"
    return "wot"
