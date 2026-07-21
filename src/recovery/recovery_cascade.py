"""Facade for the four-tier recovery cascade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.contracts.types import ExecutionResult, SkillCall, SkillTuple
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
    delay_s: float = 0.0
    rollback_call: SkillCall | None = None


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
    selected_reason: str = ""
    retry_delay_s: float = 0.0
    rollback_call: SkillCall | None = None


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
        action, _ = self._decide_and_trace(result, skill_tuple, cognitive_map, context, available_backends)
        return action

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
        _, trace = self.select_with_trace(
            result,
            skill_tuple,
            cognitive_map,
            available_backends=available_backends,
            retry_count=retry_count,
            tried_backends=tried_backends,
            rollback_available=rollback_available,
            boundary=boundary,
        )
        return trace

    def select_with_trace(
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
    ) -> tuple[RecoveryAction, RecoveryTrace]:
        context = RecoveryContext(
            skill_id=skill_tuple.skill_id,
            failed_backend=result.backend_used,
            failure_type=result.failure_reason or "execution_failed",
            retry_count=retry_count,
            tried_backends=tried_backends or [],
            rollback_available=rollback_available,
        )
        action, steps = self._decide_and_trace(result, skill_tuple, cognitive_map, context, available_backends)
        trace = RecoveryTrace(
            failure_type=context.failure_type,
            boundary=boundary,
            steps=steps,
            selected_action=action.action_type,
            selected_tier=action.recovery_tier,
            selected_backend=action.backend,
            selected_reason=action.reason,
            retry_delay_s=action.delay_s,
            rollback_call=action.rollback_call,
        )
        return action, trace

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
        retry_allowed = retry.should_retry and skill_tuple.idempotent and not skill_tuple.irreversible
        retry_reason = retry.reason
        if retry.should_retry and not retry_allowed:
            retry_reason = "transient failure but skill is not declared idempotent"
        steps.append(RecoveryDecisionStep(1, "retry", True, retry_allowed, retry_reason))
        if retry_allowed:
            return (
                RecoveryAction(
                    "retry",
                    context.failed_backend,
                    1,
                    retry_reason,
                    delay_s=retry.delay_s,
                ),
                steps,
            )

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
        rollback_allowed = rollback.should_rollback and context.rollback_available
        rollback_reason = rollback.reason
        if rollback.should_rollback and not context.rollback_available:
            rollback_reason = "rollback spec exists but no rollback executor is available"
        steps.append(RecoveryDecisionStep(3, "rollback", True, rollback_allowed, rollback_reason))
        if rollback_allowed:
            return (
                RecoveryAction(
                    "rollback",
                    recovery_tier=3,
                    reason=rollback_reason,
                    rollback_call=rollback.rollback_call,
                ),
                steps,
            )

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
