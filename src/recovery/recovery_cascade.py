"""Facade for the four-tier recovery cascade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.contracts.types import ExecutionResult, SkillTuple
from src.recovery.human_escalation import HumanEscalationPolicy
from src.recovery.reroute_policy import ReroutePolicy
from src.recovery.retry_policy import RetryPolicy
from src.recovery.rollback_policy import RollbackPolicy
from src.runtime.cognitive_map import CognitiveMap

RecoveryActionType = Literal["retry", "reroute", "rollback", "escalate_human", "abort"]


@dataclass
class RecoveryContext:
    skill_id: str
    failed_backend: str
    failure_type: str
    retry_count: int = 0
    tried_backends: list[str] = field(default_factory=list)
    rollback_available: bool = False


@dataclass
class RecoveryAction:
    action_type: RecoveryActionType
    backend: str = ""
    recovery_tier: int = 0
    reason: str = ""


@dataclass
class RecoveryDecisionStep:
    tier: int
    policy: str
    considered: bool
    selected: bool
    reason: str
    backend: str = ""


@dataclass
class RecoveryTrace:
    failure_type: str
    boundary: str
    steps: list[RecoveryDecisionStep]
    selected_action: RecoveryActionType
    selected_tier: int
    selected_backend: str = ""


class RecoveryCascade:
    def __init__(self) -> None:
        self.retry_policy = RetryPolicy()
        self.reroute_policy = ReroutePolicy()
        self.rollback_policy = RollbackPolicy()
        self.escalation_policy = HumanEscalationPolicy()

    def decide(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        cognitive_map: CognitiveMap,
        context: RecoveryContext,
        available_backends: list[str],
    ) -> RecoveryAction:
        if result.success:
            return RecoveryAction("abort", recovery_tier=0, reason="recovery not needed for successful result")

        unresolved_conflict = bool(cognitive_map.unresolved_conflicts())
        if unresolved_conflict and skill_tuple.safety_level == "high":
            return RecoveryAction(
                "escalate_human",
                recovery_tier=4,
                reason="high-safety skill blocked by unresolved perceptual conflict",
            )

        retry = self.retry_policy.decide(result, attempt=context.retry_count + 1)
        if retry.should_retry:
            return RecoveryAction("retry", context.failed_backend, 1, retry.reason)

        reroute = self.reroute_policy.decide(
            skill_tuple,
            failed_backend=context.failed_backend,
            available_backends=available_backends,
            tried_backends=context.tried_backends,
        )
        if reroute.should_reroute:
            return RecoveryAction("reroute", reroute.selected_backend, 2, reroute.reason)

        rollback = self.rollback_policy.decide(skill_tuple, cognitive_map)
        if rollback.should_rollback:
            return RecoveryAction("rollback", recovery_tier=3, reason=rollback.reason)

        escalation = self.escalation_policy.decide(
            skill_tuple,
            automated_options_exhausted=True,
            unresolved_conflict=unresolved_conflict,
        )
        if escalation.should_escalate:
            return RecoveryAction("escalate_human", recovery_tier=4, reason=escalation.reason)

        return RecoveryAction("abort", recovery_tier=4, reason="no recovery action available")

    def decide_with_trace(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        cognitive_map: CognitiveMap,
        *,
        available_backends: list[str],
        retry_count: int = 0,
        tried_backends: list[str] | None = None,
        rollback_available: bool = False,
        boundary: str = "",
    ) -> RecoveryTrace:
        context = RecoveryContext(
            skill_id=skill_tuple.skill_id,
            failed_backend=result.backend_used,
            failure_type=result.failure_reason or "execution_failed",
            retry_count=retry_count,
            tried_backends=tried_backends or [],
            rollback_available=rollback_available,
        )
        action, steps = self._decide_and_trace(result, skill_tuple, cognitive_map, context, available_backends)
        return RecoveryTrace(
            failure_type=context.failure_type,
            boundary=boundary,
            steps=steps,
            selected_action=action.action_type,
            selected_tier=action.recovery_tier,
            selected_backend=action.backend,
        )

    def _decide_and_trace(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        cognitive_map: CognitiveMap,
        context: RecoveryContext,
        available_backends: list[str],
    ) -> tuple[RecoveryAction, list[RecoveryDecisionStep]]:
        steps: list[RecoveryDecisionStep] = []
        if result.success:
            action = RecoveryAction("abort", recovery_tier=0, reason="recovery not needed for successful result")
            steps.append(RecoveryDecisionStep(0, "none", True, True, action.reason))
            return action, steps

        unresolved_conflict = bool(cognitive_map.unresolved_conflicts())
        if unresolved_conflict and skill_tuple.safety_level == "high":
            action = RecoveryAction(
                "escalate_human",
                recovery_tier=4,
                reason="high-safety skill blocked by unresolved perceptual conflict",
            )
            steps.append(RecoveryDecisionStep(4, "human_escalation", True, True, action.reason))
            return action, steps

        retry = self.retry_policy.decide(result, attempt=context.retry_count + 1)
        steps.append(RecoveryDecisionStep(1, "retry", True, retry.should_retry, retry.reason))
        if retry.should_retry:
            return RecoveryAction("retry", context.failed_backend, 1, retry.reason), steps

        reroute = self.reroute_policy.decide(
            skill_tuple,
            failed_backend=context.failed_backend,
            available_backends=available_backends,
            tried_backends=context.tried_backends,
        )
        steps.append(
            RecoveryDecisionStep(
                2,
                "reroute",
                True,
                reroute.should_reroute,
                reroute.reason,
                reroute.selected_backend,
            )
        )
        if reroute.should_reroute:
            return RecoveryAction("reroute", reroute.selected_backend, 2, reroute.reason), steps

        rollback = self.rollback_policy.decide(skill_tuple, cognitive_map)
        steps.append(RecoveryDecisionStep(3, "rollback", True, rollback.should_rollback, rollback.reason))
        if rollback.should_rollback:
            return RecoveryAction("rollback", recovery_tier=3, reason=rollback.reason), steps

        escalation = self.escalation_policy.decide(
            skill_tuple,
            automated_options_exhausted=True,
            unresolved_conflict=unresolved_conflict,
        )
        steps.append(RecoveryDecisionStep(4, "human_escalation", True, escalation.should_escalate, escalation.reason))
        if escalation.should_escalate:
            return RecoveryAction("escalate_human", recovery_tier=4, reason=escalation.reason), steps

        action = RecoveryAction("abort", recovery_tier=4, reason="no recovery action available")
        steps.append(RecoveryDecisionStep(4, "abort", True, True, action.reason))
        return action, steps
