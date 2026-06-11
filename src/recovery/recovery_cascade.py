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
