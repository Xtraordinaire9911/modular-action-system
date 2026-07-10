"""Runtime backend routing control based on CognitiveMap state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    """Rule-first router for the smart-room vertical slice.

    An optional ``policy_overlay`` carries routing adjustments learned from
    approved adaptation proposals (see ``src.adaptation.policy_store``). It is
    consumed by duck typing so the runtime keeps no dependency on adaptation;
    when ``None`` the router behaves exactly as before.
    """

    def __init__(self, policy_overlay: Any | None = None) -> None:
        self._overlay = policy_overlay

    def _preferred_backend(self, skill_id: str) -> str:
        if self._overlay is not None:
            override = self._overlay.preferred_backend(skill_id)
            if override:
                return override
        return _preferred_backend(skill_id)

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

        preferred = self._preferred_backend(skill_call.skill_id)
        best = max(
            candidates,
            key=lambda affordance: _score(
                affordance, previous_failures, preferred, self._overlay, skill_call.skill_id
            ),
        )
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
    policy_overlay: Any | None = None,
    skill_id: str = "",
) -> float:
    source_bias = {"wot": 0.05, "dom": 0.03, "visual": 0.0, "system": -0.1}.get(affordance.source, 0.0)
    preferred_bonus = 0.1 if affordance.source == preferred_backend else 0.0
    failure_penalty = 0.15 * (previous_failures or {}).get(affordance.source, 0)
    learned_penalty = (
        policy_overlay.skill_backend_penalty(skill_id, affordance.source) if policy_overlay is not None else 0.0
    )
    return affordance.confidence + source_bias + preferred_bonus - failure_penalty - learned_penalty
