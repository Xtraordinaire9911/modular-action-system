"""Compatibility adapters for the canonical runtime backend router.

Existing evaluation and skill-level callers keep their public APIs, but all
candidate filtering, ranking, and decision construction now run through
``src.runtime.backend_router.RuntimeBackendRouter``.
"""

from __future__ import annotations

from typing import Any

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.contracts.types import Affordance, SkillCall, SkillTuple
from src.runtime.backend_router import (
    DEFAULT_COST,
    CostAwareRoutingScorer,
    RoutingDecision,
    RuntimeBackendRouter,
)
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance

_AFF_SOURCE_TO_BACKEND = {"DOM": "dom", "WOT": "wot", "VISUAL": "visual"}
_COST_DEFAULTS = {"dom": 0.1, "visual": 0.5, "wot": 0.05}


class CostAwareRouter:
    """Legacy affordance-candidate API backed by RuntimeBackendRouter."""

    def __init__(
        self,
        tracker: BackendConfidenceTracker | None = None,
        *,
        lambdas: tuple[float, float, float] = (1.0, 1.0, 1.0),
        mode: str = "full",
        cost: dict[str, float] | None = None,
    ) -> None:
        scorer = CostAwareRoutingScorer(tracker, lambdas=lambdas, cost=cost or dict(DEFAULT_COST))
        self.core = RuntimeBackendRouter(scorer=scorer, mode=mode)

    def route(self, skill: SkillTuple, candidates: dict[str, Affordance]) -> RoutingDecision:
        cognitive_map = _candidate_map(skill, candidates)
        return self.core.select_backend(
            SkillCall(skill.skill_id, {}, preferred_backends=list(skill.preferred_backends)),
            cognitive_map,
        )

    def observe(self, backend: str, *, success: bool, latency_ms: float) -> None:
        self.core.record_outcome(backend, success=success, latency_ms=latency_ms)

    def to_dict(self) -> dict[str, Any]:
        return self.core.snapshot()


class BackendRouter:
    """Legacy skill-level API backed by the same canonical routing core."""

    def __init__(
        self,
        tracker: BackendConfidenceTracker | None = None,
        lambda_cost: float = 0.4,
        lambda_reliability: float = 0.4,
        lambda_latency: float = 0.2,
    ) -> None:
        scorer = CostAwareRoutingScorer(
            tracker,
            lambdas=(lambda_cost, lambda_reliability, lambda_latency),
            cost=dict(_COST_DEFAULTS),
        )
        self.core = RuntimeBackendRouter(scorer=scorer)

    def route(
        self,
        skill_call: SkillCall,
        skill_tuple: SkillTuple,
        available: list[str],
        exclude: list[str] | None = None,
    ) -> RoutingDecision:
        candidates = {
            backend: _synthetic_affordance(backend, skill_tuple.skill_id)
            for backend in skill_tuple.allowed_backends
            if backend in available and backend not in (exclude or [])
        }
        cognitive_map = _candidate_map(skill_tuple, candidates)
        call = SkillCall(
            skill_call.skill_id,
            dict(skill_call.params),
            priority=skill_call.priority,
            required_postconditions=list(skill_call.required_postconditions),
            preferred_backends=list(skill_call.preferred_backends or skill_tuple.preferred_backends),
        )
        decision = self.core.select_backend(call, cognitive_map)
        if not candidates:
            decision.reason = "no available backend satisfies skill constraints"
        return decision

    def record_outcome(self, backend: str, success: bool, latency_ms: float) -> None:
        self.core.record_outcome(backend, success=success, latency_ms=latency_ms)


def _candidate_map(skill: SkillTuple, candidates: dict[str, Affordance]) -> CognitiveMap:
    cognitive_map = CognitiveMap(task_id=f"routing:{skill.skill_id}")
    for key, affordance in candidates.items():
        backend = _AFF_SOURCE_TO_BACKEND.get(affordance.source, key.lower())
        if backend not in skill.allowed_backends:
            continue
        cognitive_map.add_affordance(
            RuntimeAffordance(
                id=affordance.id,
                source=backend,  # type: ignore[arg-type]
                entity_id=str(affordance.locator.get("entity_id") or affordance.id),
                action_name=affordance.action,
                action_type=affordance.type,
                confidence=affordance.confidence,
                grounding=dict(affordance.locator),
                skill_names=[skill.skill_id],
            )
        )
    return cognitive_map


def _synthetic_affordance(backend: str, skill_id: str) -> Affordance:
    source = {"dom": "DOM", "wot": "WOT", "visual": "VISUAL"}.get(backend, "WOT")
    return Affordance(
        id=f"{backend}:{skill_id}",
        source=source,  # type: ignore[arg-type]
        type="action",
        label=skill_id,
        action=skill_id,
        locator={"entity_id": skill_id},
        confidence=1.0,
    )
