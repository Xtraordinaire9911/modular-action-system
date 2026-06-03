"""Main runtime orchestration skeleton """

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.contracts.types import ExecutionResult, Observation, SkillCall, SkillTuple
from src.recovery.human_escalation import HumanEscalationPolicy
from src.recovery.retry_policy import RetryPolicy
from src.recovery.rollback_policy import RollbackPolicy
from src.recovery.reroute_policy import ReroutePolicy
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.state_machine import RuntimeState
from src.safety.unsafe_action_detector import UnsafeActionDetector
from src.verification.postcondition_checker import PostconditionChecker
from src.verification.precondition_checker import PreconditionChecker


class Executor(Protocol):
    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        ...


@dataclass
class RuntimeStepResult:
    state: RuntimeState
    execution_result: ExecutionResult | None
    recovery_tier: int | None = None
    selected_backend: str = ""
    reason: str = ""


class ContinuousInteractionManager:
    """Glue layer between skill contracts, executors, verification, and recovery."""

    def __init__(
        self,
        skill_library: dict[str, SkillTuple],
        executors: dict[str, Executor],
        cognitive_map: CognitiveMap,
    ) -> None:
        self.skill_library = skill_library
        self.executors = executors
        self.cognitive_map = cognitive_map
        self.state = RuntimeState.IDLE
        self.preconditions = PreconditionChecker()
        self.postconditions = PostconditionChecker()
        self.retry_policy = RetryPolicy()
        self.reroute_policy = ReroutePolicy()
        self.rollback_policy = RollbackPolicy()
        self.escalation_policy = HumanEscalationPolicy()
        self.safety = UnsafeActionDetector()

    async def run_skill(self, skill_call: SkillCall, observation: Observation) -> RuntimeStepResult:
        skill_tuple = self.skill_library[skill_call.skill_id]
        self.cognitive_map.set_current_skill(skill_call)
        self.cognitive_map.update_from_observation(observation)

        safety_decision = self.safety.decide(skill_call, skill_tuple)
        if not safety_decision.allowed:
            self.state = RuntimeState.ESCALATED
            return RuntimeStepResult(self.state, None, recovery_tier=4, reason=safety_decision.reason)

        self.state = RuntimeState.PRECHECK
        if not self.preconditions.passes(skill_tuple.preconditions, self.cognitive_map):
            self.state = RuntimeState.RECOVERING
            return RuntimeStepResult(self.state, None, recovery_tier=4, reason="precondition failed")

        backend = self._select_backend(skill_call, skill_tuple)
        if backend == "":
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(self.state, None, reason="no backend available")

        self.state = RuntimeState.EXECUTING
        result = await self.executors[backend].execute(skill_call, observation)
        self.cognitive_map.record_execution_result(result)

        if not result.success:
            retry = self.retry_policy.decide(result, attempt=1)
            if retry.should_retry:
                self.state = RuntimeState.RECOVERING
                return RuntimeStepResult(self.state, result, recovery_tier=1, selected_backend=backend, reason=retry.reason)

            reroute = self.reroute_policy.decide(
                skill_tuple,
                failed_backend=backend,
                available_backends=list(self.executors.keys()),
                tried_backends=[backend],
            )
            if reroute.should_reroute:
                self.state = RuntimeState.RECOVERING
                return RuntimeStepResult(
                    self.state,
                    result,
                    recovery_tier=2,
                    selected_backend=reroute.selected_backend,
                    reason=reroute.reason,
                )

            escalation = self.escalation_policy.decide(skill_tuple, automated_options_exhausted=True)
            self.state = RuntimeState.ESCALATED if escalation.should_escalate else RuntimeState.FAILED
            return RuntimeStepResult(self.state, result, recovery_tier=4, selected_backend=backend, reason=escalation.reason)

        self.state = RuntimeState.VERIFYING
        if not self.postconditions.passes(skill_tuple.postconditions, self.cognitive_map):
            rollback = self.rollback_policy.decide(skill_tuple, self.cognitive_map)
            self.state = RuntimeState.RECOVERING
            return RuntimeStepResult(
                self.state,
                result,
                recovery_tier=3 if rollback.should_rollback else 4,
                selected_backend=backend,
                reason=rollback.reason,
            )

        self.state = RuntimeState.COMPLETED
        return RuntimeStepResult(self.state, result, selected_backend=backend, reason="skill completed")

    def _select_backend(self, skill_call: SkillCall, skill_tuple: SkillTuple) -> str:
        preferences = skill_call.preferred_backends or skill_tuple.preferred_backends
        for backend in preferences:
            if backend in skill_tuple.allowed_backends and backend in self.executors:
                return backend
        for backend in skill_tuple.allowed_backends:
            if backend in self.executors:
                return backend
        return ""
