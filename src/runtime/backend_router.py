"""Runtime backend routing control based on CognitiveMap state."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import SkillCall
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance


@dataclass
class RoutingDecision:
    backend: str
    affordance_id: str
    reason: str
    confidence: float


@dataclass
class RecoveryRoutingContext:
    exclude_backends: list[str]
    previous_failures: dict[str, int] | None = None


class RuntimeBackendRouter:
    """Rule-first router for the smart-room vertical slice."""

    def select_backend(
        self,
        skill_call: SkillCall,
        cognitive_map: CognitiveMap,
        recovery_context: RecoveryRoutingContext | None = None,
    ) -> RoutingDecision:
        excluded = set(recovery_context.exclude_backends if recovery_context else [])
        previous_failures = recovery_context.previous_failures if recovery_context else None
        all_affordances = cognitive_map.get_affordances_for_skill(skill_call.skill_id)
        candidates = [affordance for affordance in all_affordances if affordance.source not in excluded]
        if not candidates:
            if all_affordances and excluded:
                return RoutingDecision("", "", "all candidate backends excluded by recovery context", 0.0)
            return RoutingDecision("", "", "no affordance available", 0.0)

        preferred = _preferred_backend(skill_call.skill_id)
        best = max(candidates, key=lambda affordance: _score(affordance, previous_failures, preferred))
        reason = f"selected {best.source} for {skill_call.skill_id}"
        if best.source != preferred:
            reason += f" because preferred backend {preferred} was unavailable"
        if previous_failures and previous_failures.get(best.source, 0) > 0:
            reason += "; selected despite prior failures because no stronger candidate exists"
        return RoutingDecision(best.source, best.id, reason, best.confidence)


def _preferred_backend(skill_id: str) -> str:
    if skill_id in {"set_temperature", "set_lighting", "turn_on_projector", "verify_readiness"}:
        return "wot"
    if skill_id == "confirm_booking":
        return "dom"
    return "wot"


def _score(
    affordance: RuntimeAffordance,
    previous_failures: dict[str, int] | None = None,
    preferred_backend: str | None = None,
) -> float:
    source_bias = {"wot": 0.05, "dom": 0.03, "visual": 0.0, "system": -0.1}.get(affordance.source, 0.0)
    preferred_bonus = 0.1 if affordance.source == preferred_backend else 0.0
    failure_penalty = 0.15 * (previous_failures or {}).get(affordance.source, 0)
    return affordance.confidence + source_bias + preferred_bonus - failure_penalty
