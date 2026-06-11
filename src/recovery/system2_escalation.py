"""System 2 escalation trigger and structured recovery payload."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.runtime.cognitive_map import CognitiveMap, Conflict, RuntimeAffordance

System2Decision = Literal["active_perception", "reroute", "rollback", "escalate_human", "abort"]


@dataclass
class System2Payload:
    failed_skill_id: str
    failed_backend: str | None
    failure_type: str
    cognitive_state_summary: dict
    unresolved_conflicts: list[Conflict]
    available_affordances: list[RuntimeAffordance]
    allowed_recovery_actions: list[str] = field(default_factory=lambda: ["retry", "reroute", "rollback", "ask_human"])


@dataclass
class System2TriggerDecision:
    should_trigger: bool
    reason: str
    payload: System2Payload | None = None


class System2EscalationPolicy:
    def __init__(self, confidence_threshold: float = 0.9) -> None:
        self.confidence_threshold = confidence_threshold

    def decide(
        self,
        skill_id: str,
        cognitive_map: CognitiveMap,
        failed_backend: str | None = None,
        failure_type: str = "",
        confidence: float = 1.0,
        postcondition_failed: bool = False,
        backend_unavailable: bool = False,
        unsafe_action: bool = False,
    ) -> System2TriggerDecision:
        reason = self._reason(
            cognitive_map=cognitive_map,
            confidence=confidence,
            postcondition_failed=postcondition_failed,
            backend_unavailable=backend_unavailable,
            unsafe_action=unsafe_action,
        )
        if reason == "":
            return System2TriggerDecision(False, "system1_allowed")
        payload = System2Payload(
            failed_skill_id=skill_id,
            failed_backend=failed_backend,
            failure_type=failure_type or reason,
            cognitive_state_summary=cognitive_map.snapshot(),
            unresolved_conflicts=cognitive_map.unresolved_conflicts(),
            available_affordances=list(cognitive_map.runtime_affordances.values()),
        )
        return System2TriggerDecision(True, reason, payload)

    def _reason(
        self,
        cognitive_map: CognitiveMap,
        confidence: float,
        postcondition_failed: bool,
        backend_unavailable: bool,
        unsafe_action: bool,
    ) -> str:
        if unsafe_action:
            return "unsafe_action_requires_confirmation"
        if any(conflict.severity == "high" for conflict in cognitive_map.unresolved_conflicts()):
            return "high_severity_conflict"
        if postcondition_failed:
            return "postcondition_failed"
        if backend_unavailable:
            return "backend_unavailable"
        if confidence < self.confidence_threshold:
            return "low_confidence"
        return ""


def suggest_system2_decision(payload: System2Payload) -> dict:
    """Deterministic placeholder for a schema-constrained System 2 response."""
    if payload.unresolved_conflicts:
        first = payload.unresolved_conflicts[0]
        return {
            "decision": "active_perception",
            "probe_action": "repoll_wot_or_refresh_dom",
            "target_entity": first.entity_id,
            "reason": first.description,
            "confidence": 0.86,
        }
    if payload.failure_type == "postcondition_failed":
        return {
            "decision": "active_perception",
            "probe_action": "refresh_state",
            "target_entity": payload.failed_skill_id,
            "reason": "Postcondition did not match observed state.",
            "confidence": 0.82,
        }
    return {
        "decision": "reroute",
        "probe_action": None,
        "target_entity": payload.failed_skill_id,
        "reason": payload.failure_type,
        "confidence": 0.8,
    }
