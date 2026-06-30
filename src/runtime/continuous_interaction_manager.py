"""Main runtime orchestration skeleton"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from src.contracts.types import ExecutionResult, Observation, SkillCall, SkillTuple

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.skill_library.library import SkillLibrary
from src.recovery.recovery_cascade import RecoveryCascade, RecoveryContext
from src.runtime.backend_router import RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.state_machine import RuntimeState
from src.safety.unsafe_action_detector import UnsafeActionDetector
from src.verification.conflict_detector import EpistemicArbiter
from src.verification.postcondition_checker import PostconditionChecker
from src.verification.precondition_checker import PreconditionChecker


class Executor(Protocol):
    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult: ...


@dataclass
class RuntimeStepResult:
    state: RuntimeState
    execution_result: ExecutionResult | None
    recovery_tier: int | None = None
    selected_backend: str = ""
    reason: str = ""
    routing_reason: str = ""
    conflict_ids: list[str] = field(default_factory=list)


class ContinuousInteractionManager:
    """Glue layer between skill contracts, executors, verification, and recovery."""

    def __init__(
        self,
        skill_library: "SkillLibrary | Mapping[str, SkillTuple]",
        executors: dict[str, Executor],
        cognitive_map: CognitiveMap,
        backend_router: RuntimeBackendRouter | None = None,
        epistemic_arbiter: EpistemicArbiter | None = None,
        recovery_cascade: RecoveryCascade | None = None,
    ) -> None:
        # Accept the typed SkillLibrary (single source of truth) or a plain
        # mapping; normalize to a dict so lookups stay O(1) either way.
        self.skill_library: dict[str, SkillTuple] = (
            skill_library.as_dict() if hasattr(skill_library, "as_dict") else dict(skill_library)
        )
        self.executors = executors
        self.cognitive_map = cognitive_map
        self.state = RuntimeState.IDLE
        self.preconditions = PreconditionChecker()
        self.postconditions = PostconditionChecker()
        self.backend_router = backend_router or RuntimeBackendRouter()
        self.epistemic_arbiter = epistemic_arbiter or EpistemicArbiter()
        self.recovery_cascade = recovery_cascade or RecoveryCascade()
        self.safety = UnsafeActionDetector()

    async def run_skill(self, skill_call: SkillCall, observation: Observation) -> RuntimeStepResult:
        skill_tuple = self.skill_library.get(skill_call.skill_id)
        if skill_tuple is None:
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(
                self.state,
                None,
                reason=f"unknown skill: {skill_call.skill_id}",
            )
        self.cognitive_map.set_current_skill(skill_call)
        self.cognitive_map.update_from_observation(observation)

        conflicts = self.epistemic_arbiter.check(self.cognitive_map)
        if self.epistemic_arbiter.should_halt_system1(conflicts):
            self.state = RuntimeState.ESCALATED
            strongest = max(conflicts, key=lambda conflict: conflict.conflict_mass)
            return RuntimeStepResult(
                self.state,
                None,
                recovery_tier=4,
                reason=f"sensory conflict detected: {strongest.description}",
                conflict_ids=[conflict.id for conflict in conflicts],
            )

        safety_decision = self.safety.decide(skill_call, skill_tuple)
        if not safety_decision.allowed:
            self.state = RuntimeState.ESCALATED
            return RuntimeStepResult(self.state, None, recovery_tier=4, reason=safety_decision.reason)

        self.state = RuntimeState.PRECHECK
        if not self.preconditions.passes(skill_tuple.preconditions, self.cognitive_map):
            self.state = RuntimeState.RECOVERING
            return RuntimeStepResult(self.state, None, recovery_tier=4, reason="precondition failed")

        self.state = RuntimeState.ROUTING
        backend, routing_reason = self._select_backend(skill_call, skill_tuple)
        if backend == "":
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(self.state, None, reason="no backend available", routing_reason=routing_reason)

        self.state = RuntimeState.EXECUTING
        try:
            result = await self.executors[backend].execute(skill_call, observation)
        except Exception as exc:
            result = ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=backend,
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"executor_exception:{type(exc).__name__}",
            )
            return self._recover_from_result(
                result,
                skill_tuple,
                failed_backend=backend,
                tried_backends=[backend],
                routing_reason=routing_reason,
            )
        self.cognitive_map.record_execution_result(result)

        if not result.success:
            return self._recover_from_result(
                result,
                skill_tuple,
                failed_backend=backend,
                tried_backends=[backend],
                routing_reason=routing_reason,
            )

        self.state = RuntimeState.VERIFYING
        if not self.postconditions.passes(skill_tuple.postconditions, self.cognitive_map):
            postcondition_failure = ExecutionResult(
                skill_id=result.skill_id,
                backend_used=backend,
                success=False,
                latency_ms=result.latency_ms,
                confidence=result.confidence,
                failure_reason="postcondition_failed",
                raw_observation_delta=result.raw_observation_delta,
            )
            return self._recover_from_result(
                postcondition_failure,
                skill_tuple,
                failed_backend=backend,
                tried_backends=[backend],
                execution_result=result,
                routing_reason=routing_reason,
            )

        self.state = RuntimeState.COMPLETED
        return RuntimeStepResult(
            self.state,
            result,
            selected_backend=backend,
            reason="skill completed",
            routing_reason=routing_reason,
        )

    def _select_backend(self, skill_call: SkillCall, skill_tuple: SkillTuple) -> tuple[str, str]:
        routing_decision = self.backend_router.select_backend(skill_call, self.cognitive_map)
        if (
            routing_decision.backend
            and routing_decision.backend in skill_tuple.allowed_backends
            and routing_decision.backend in self.executors
        ):
            return routing_decision.backend, routing_decision.reason

        preferences = skill_call.preferred_backends or skill_tuple.preferred_backends
        for backend in preferences:
            if backend in skill_tuple.allowed_backends and backend in self.executors:
                return backend, f"fallback preferred backend {backend}"
        for backend in skill_tuple.allowed_backends:
            if backend in self.executors:
                return backend, f"fallback allowed backend {backend}"
        if routing_decision.reason:
            return "", routing_decision.reason
        return "", "no allowed executor backend available"

    def _recover_from_result(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        *,
        failed_backend: str,
        tried_backends: list[str],
        execution_result: ExecutionResult | None = None,
        routing_reason: str = "",
    ) -> RuntimeStepResult:
        recovery = self.recovery_cascade.decide(
            result,
            skill_tuple,
            self.cognitive_map,
            RecoveryContext(
                skill_id=result.skill_id,
                failed_backend=failed_backend,
                failure_type=result.failure_reason or "execution_failed",
                tried_backends=tried_backends,
            ),
            available_backends=list(self.executors.keys()),
        )
        returned_result = execution_result or result
        if recovery.action_type == "escalate_human":
            self.state = RuntimeState.ESCALATED
        elif recovery.action_type == "abort":
            self.state = RuntimeState.FAILED
        else:
            self.state = RuntimeState.RECOVERING
        selected_backend = recovery.backend or failed_backend
        return RuntimeStepResult(
            self.state,
            returned_result,
            recovery_tier=recovery.recovery_tier,
            selected_backend=selected_backend,
            reason=recovery.reason,
            routing_reason=routing_reason,
        )
