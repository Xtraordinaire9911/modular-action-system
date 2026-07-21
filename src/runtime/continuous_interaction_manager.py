"""Main runtime orchestration skeleton"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Protocol

from src.adaptation.failure_boundary import FailureAnalysis
from src.adaptation.llm_judge import LLMJudge, LLMJudgeInput, LLMJudgeOutputError, LLMJudgeUnavailable
from src.adaptation.rule_classifier import RuleFailureClassifier
from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.recovery.recovery_cascade import RecoveryCascade
from src.runtime.action_context import build_action_context
from src.runtime.affordance_controller import AffordanceController
from src.runtime.backend_router import RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_observation import LiveRuntimeObservation
from src.runtime.plan_validator import PlanValidator
from src.runtime.primitive_action import PrimitiveAction
from src.runtime.state_machine import RuntimeState
from src.runtime.system2_planner import System2Planner
from src.safety.unsafe_action_detector import UnsafeActionDetector
from src.verification.active_perception import ActivePerceptionResolver
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
    failure_boundary: str = ""
    failure_type: str = ""
    recovery_trace: list[dict[str, object]] = field(default_factory=list)
    llm_failure_boundary: str = ""
    llm_failure_type: str = ""
    llm_judge_evidence: list[str] = field(default_factory=list)
    active_perception_trace: list[dict[str, object]] = field(default_factory=list)
    fusion_decision: dict[str, object] = field(default_factory=dict)
    primitive_plan: list[dict[str, object]] = field(default_factory=list)
    plan_validation_errors: list[str] = field(default_factory=list)


class ContinuousInteractionManager:
    """Glue layer between skill contracts, executors, verification, and recovery."""

    def __init__(
        self,
        skill_library: dict[str, SkillTuple],
        executors: dict[str, Executor],
        cognitive_map: CognitiveMap,
        backend_router: RuntimeBackendRouter | None = None,
        epistemic_arbiter: EpistemicArbiter | None = None,
        recovery_cascade: RecoveryCascade | None = None,
        llm_judge: LLMJudge | None = None,
        use_llm_judge: bool = False,
        active_perception_resolver: ActivePerceptionResolver | None = None,
        system2_planner: System2Planner | None = None,
        plan_validator: PlanValidator | None = None,
    ) -> None:
        self.skill_library = skill_library
        self.executors = executors
        self.cognitive_map = cognitive_map
        self.state = RuntimeState.IDLE
        self.preconditions = PreconditionChecker()
        self.postconditions = PostconditionChecker()
        self.backend_router = backend_router or RuntimeBackendRouter()
        self.epistemic_arbiter = epistemic_arbiter or EpistemicArbiter()
        self.recovery_cascade = recovery_cascade or RecoveryCascade()
        self.safety = UnsafeActionDetector()
        self.failure_classifier = RuleFailureClassifier()
        self.llm_judge = llm_judge
        self.use_llm_judge = use_llm_judge
        self.active_perception_resolver = active_perception_resolver
        self.system2_planner = system2_planner or System2Planner(AffordanceController())
        self.plan_validator = plan_validator or PlanValidator()

    async def run_skill(self, skill_call: SkillCall, observation: Observation) -> RuntimeStepResult:
        skill_tuple = self.skill_library.get(skill_call.skill_id)
        if skill_tuple is None:
            analysis = self.failure_classifier.classify_unknown_skill(skill_call.skill_id)
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(
                self.state,
                None,
                reason=f"unknown skill: {skill_call.skill_id}",
                failure_boundary=analysis.boundary.value,
                failure_type=analysis.failure_type,
            )
        self.cognitive_map.set_current_skill(skill_call)
        self.cognitive_map.update_from_observation(observation)

        gate = await self._run_fusion_gate(observation)
        if gate is not None:
            return gate
        active_perception_trace = self._last_active_perception_trace
        fusion_payload = self._last_fusion_decision

        if self.cognitive_map.unresolved_conflicts():
            self.state = RuntimeState.ESCALATED
            conflicts = self.cognitive_map.unresolved_conflicts()
            strongest = max(conflicts, key=lambda conflict: conflict.conflict_mass)
            return RuntimeStepResult(
                self.state,
                None,
                recovery_tier=4,
                reason=f"sensory conflict detected: {strongest.description}",
                conflict_ids=[conflict.id for conflict in conflicts],
                failure_boundary="recoverable_execution_failure",
                failure_type="sensory_conflict",
                active_perception_trace=active_perception_trace,
                fusion_decision=fusion_payload,
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
            analysis = self.failure_classifier.classify_no_backend(
                skill_id=skill_call.skill_id,
                allowed_backends=skill_tuple.allowed_backends,
                available_backends=list(self.executors),
            )
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(
                self.state,
                None,
                reason="no backend available",
                routing_reason=routing_reason,
                failure_boundary=analysis.boundary.value,
                failure_type=analysis.failure_type,
                fusion_decision=fusion_payload,
            )

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
            active_perception_trace=active_perception_trace,
            fusion_decision=fusion_payload,
        )

    async def run_goal(
        self,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, object] | None = None,
        observation: Observation | None = None,
        goal_spec: GoalSpec | None = None,
    ) -> RuntimeStepResult:
        """Run a bounded no-durable-skill goal over current affordances.

        This is the action-system zero-shot path: an upstream component has
        already provided a structured goal. The runtime scans the environment,
        builds an ActionContext, plans typed primitive actions over affordance
        IDs, validates the plan, executes through existing backend executors,
        and verifies the declared goal state.
        """

        if goal_spec is not None:
            validation_errors = goal_spec.validate()
            goal_id = goal_spec.goal_id
            goal_state = goal_spec.goal_state
            parameters = dict(goal_spec.parameters)
            if validation_errors:
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    None,
                    recovery_tier=4,
                    reason="invalid goal spec",
                    failure_boundary="skill_spec_insufficient",
                    failure_type="invalid_goal_spec",
                    plan_validation_errors=validation_errors,
                )

        observation = observation or Observation()
        self.cognitive_map.set_current_skill(SkillCall(goal_id, dict(parameters or {})))
        self.cognitive_map.update_from_observation(observation)
        gate = await self._run_fusion_gate(observation)
        if gate is not None:
            return gate

        safety_constraints = ["do not use raw selectors", "do not bypass unresolved sensory conflicts"]
        if goal_spec is not None:
            safety_constraints.extend(goal_spec.safety_constraints)
        context = build_action_context(
            self.cognitive_map,
            request_type="goal_spec",
            safety_constraints=safety_constraints,
        )
        plan = self.system2_planner.plan(
            context,
            goal_id=goal_id,
            goal_state=goal_state,
            parameters=dict(parameters or {}),
        )
        primitive_plan = [_primitive_payload(action) for action in plan.actions]
        validation = self.plan_validator.validate(context, plan.actions)
        if plan.requires_escalation or not validation.valid:
            self.state = RuntimeState.ESCALATED
            return RuntimeStepResult(
                self.state,
                None,
                recovery_tier=4,
                reason=plan.reason or "primitive plan failed validation",
                failure_boundary="skill_spec_insufficient",
                failure_type="insufficient_affordance_plan",
                fusion_decision=self._last_fusion_decision,
                active_perception_trace=self._last_active_perception_trace,
                primitive_plan=primitive_plan,
                plan_validation_errors=validation.errors,
            )

        last_result: ExecutionResult | None = None
        selected_backend = ""
        for action in plan.actions:
            if action.action in {"done", "wait"}:
                continue
            if action.action == "ask_user":
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=4,
                    reason=action.expected_effect or "planner requested user clarification",
                    failure_boundary="skill_spec_insufficient",
                    failure_type="clarification_required",
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=self._last_active_perception_trace,
                    primitive_plan=primitive_plan,
                )
            affordance = self.cognitive_map.runtime_affordances.get(action.affordance_id)
            if affordance is None:
                self.state = RuntimeState.FAILED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    reason=f"unknown affordance_id: {action.affordance_id}",
                    failure_boundary="skill_spec_insufficient",
                    failure_type="unknown_affordance",
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=self._last_active_perception_trace,
                    primitive_plan=primitive_plan,
                )
            backend = affordance.source
            selected_backend = backend
            if backend not in self.executors:
                failure = ExecutionResult(
                    skill_id=goal_id,
                    backend_used=backend,
                    success=False,
                    latency_ms=0.0,
                    confidence=0.0,
                    failure_reason="no_executor_for_affordance_backend",
                )
                return self._recover_from_result(
                    failure,
                    _primitive_skill_tuple(goal_id, [backend]),
                    failed_backend=backend,
                    tried_backends=[backend],
                    routing_reason=f"primitive action {action.action} selected {backend}",
                )

            self.state = RuntimeState.EXECUTING
            primitive_call = SkillCall(
                skill_id=goal_id,
                params={
                    "primitive_action": action.action,
                    "affordance_id": action.affordance_id,
                    "value": action.value,
                    "expected_effect": action.expected_effect,
                    **dict(parameters or {}),
                },
            )
            result = await self.executors[backend].execute(primitive_call, observation)
            self.cognitive_map.record_execution_result(result)
            last_result = result
            if not result.success:
                return self._recover_from_result(
                    result,
                    _primitive_skill_tuple(goal_id, list({backend, *self.executors.keys()})),
                    failed_backend=backend,
                    tried_backends=[backend],
                    routing_reason=f"primitive action {action.action} selected {backend}",
                )

        self.state = RuntimeState.VERIFYING
        if goal_state and not self.postconditions.passes([Condition(goal_state)], self.cognitive_map):
            failure = ExecutionResult(
                skill_id=goal_id,
                backend_used=selected_backend,
                success=False,
                latency_ms=last_result.latency_ms if last_result else 0.0,
                confidence=last_result.confidence if last_result else 0.0,
                failure_reason="goal_postcondition_failed",
            )
            return self._recover_from_result(
                failure,
                _primitive_skill_tuple(goal_id, list(self.executors)),
                failed_backend=selected_backend,
                tried_backends=[selected_backend] if selected_backend else [],
                execution_result=last_result,
                routing_reason="bounded primitive goal verification",
            )

        self.state = RuntimeState.COMPLETED
        return RuntimeStepResult(
            self.state,
            last_result,
            selected_backend=selected_backend,
            reason="goal completed",
            routing_reason="bounded primitive goal plan",
            fusion_decision=self._last_fusion_decision,
            active_perception_trace=self._last_active_perception_trace,
            primitive_plan=primitive_plan,
        )

    async def run_observed_goal(
        self,
        live_observation: LiveRuntimeObservation,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, object] | None = None,
        goal_spec: GoalSpec | None = None,
    ) -> RuntimeStepResult:
        """Observe first, then run the bounded no-durable-skill goal path.

        This is the runtime-side zero-shot entry point: parsed DOM/WoT/Visual
        outputs are applied to ``CognitiveMap`` before planning, so the planner
        reasons over current affordances instead of a pre-written action chain.
        """

        observation = live_observation.apply_to(self.cognitive_map)
        return await self.run_goal(
            goal_id=goal_id,
            goal_state=goal_state,
            parameters=parameters,
            observation=observation,
            goal_spec=goal_spec,
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

    async def _run_fusion_gate(self, observation: Observation) -> RuntimeStepResult | None:
        self._last_active_perception_trace: list[dict[str, object]] = []
        fusion = self.epistemic_arbiter.fuse(self.cognitive_map)
        self._last_fusion_decision = _fusion_payload(fusion)
        conflicts = fusion.conflicts
        if not fusion.allow_system1:
            if self.active_perception_resolver is not None:
                resolution = await self.active_perception_resolver.resolve(conflicts, self.cognitive_map, observation)
                self._last_active_perception_trace = resolution.trace
                if resolution.resolved:
                    fusion = self.epistemic_arbiter.fuse(self.cognitive_map)
                    self._last_fusion_decision = _fusion_payload(fusion)
                    return None
                conflicts = [
                    conflict
                    for conflict in self.cognitive_map.unresolved_conflicts()
                    if not resolution.remaining_conflict_ids or conflict.id in resolution.remaining_conflict_ids
                ]
            if conflicts:
                self.state = RuntimeState.ESCALATED
                strongest = max(conflicts, key=lambda conflict: conflict.conflict_mass)
                return RuntimeStepResult(
                    self.state,
                    None,
                    recovery_tier=4,
                    reason=f"sensory conflict detected: {strongest.description}",
                    conflict_ids=[conflict.id for conflict in conflicts],
                    failure_boundary="recoverable_execution_failure",
                    failure_type="sensory_conflict",
                    active_perception_trace=self._last_active_perception_trace,
                    fusion_decision=self._last_fusion_decision,
                )
        return None

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
        analysis = self.failure_classifier.classify_execution_failure(result, skill_tuple, self.cognitive_map)
        llm_analysis = self._advisory_llm_analysis(result, skill_tuple, analysis, tried_backends)
        trace = self.recovery_cascade.decide_with_trace(
            result,
            skill_tuple,
            self.cognitive_map,
            available_backends=list(self.executors.keys()),
            tried_backends=tried_backends,
            boundary=analysis.boundary.value,
        )
        returned_result = execution_result or result
        if trace.selected_action == "escalate_human":
            self.state = RuntimeState.ESCALATED
        elif trace.selected_action == "abort":
            self.state = RuntimeState.FAILED
        else:
            self.state = RuntimeState.RECOVERING
        selected_backend = trace.selected_backend or failed_backend
        return RuntimeStepResult(
            self.state,
            returned_result,
            recovery_tier=trace.selected_tier,
            selected_backend=selected_backend,
            reason=_selected_recovery_reason(trace.steps),
            routing_reason=routing_reason,
            failure_boundary=analysis.boundary.value,
            failure_type=analysis.failure_type,
            recovery_trace=[asdict(step) for step in trace.steps],
            llm_failure_boundary=llm_analysis.boundary.value if llm_analysis else "",
            llm_failure_type=llm_analysis.failure_type if llm_analysis else "",
            llm_judge_evidence=llm_analysis.evidence if llm_analysis else [],
        )

    def _advisory_llm_analysis(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        rule_analysis: FailureAnalysis,
        tried_backends: list[str],
    ) -> FailureAnalysis | None:
        if not self.use_llm_judge or self.llm_judge is None:
            return None
        if rule_analysis.boundary.value in {"unsafe_governance_boundary", "architecture_gap"}:
            return None
        try:
            return self.llm_judge.judge(
                LLMJudgeInput(
                    task_id=self.cognitive_map.task_id,
                    skill_id=skill_tuple.skill_id,
                    failure_reason=result.failure_reason or "execution_failed",
                    selected_backend=result.backend_used,
                    allowed_backends=skill_tuple.allowed_backends,
                    conflict_summaries=[
                        {
                            "id": conflict.id,
                            "description": conflict.description,
                            "severity": conflict.severity,
                            "conflict_mass": conflict.conflict_mass,
                        }
                        for conflict in self.cognitive_map.unresolved_conflicts()
                    ],
                    recovery_trace=[
                        {
                            "tried_backends": tried_backends,
                            "available_backends": list(self.executors),
                        }
                    ],
                    history_summary={
                        "execution_history_count": len(self.cognitive_map.execution_history),
                    },
                )
            )
        except (LLMJudgeOutputError, LLMJudgeUnavailable, json.JSONDecodeError, KeyError, ValueError):
            return None


def _selected_recovery_reason(steps: Sequence[object]) -> str:
    for step in steps:
        if getattr(step, "selected", False):
            return str(getattr(step, "reason", ""))
    return ""


def _fusion_payload(fusion: object) -> dict[str, object]:
    return {
        "allow_system1": bool(getattr(fusion, "allow_system1", False)),
        "reason": str(getattr(fusion, "reason", "")),
        "active_perception_required": bool(getattr(fusion, "active_perception_required", False)),
        "fused_states": [asdict(state) for state in getattr(fusion, "fused_states", [])],
        "conflicts": [asdict(conflict) for conflict in getattr(fusion, "conflicts", [])],
    }


def _primitive_payload(action: PrimitiveAction) -> dict[str, object]:
    return {
        "action": action.action,
        "affordance_id": action.affordance_id,
        "value": action.value,
        "expected_effect": action.expected_effect,
    }


def _primitive_skill_tuple(goal_id: str, allowed_backends: list[str]) -> SkillTuple:
    backends = [backend for backend in allowed_backends if backend]
    return SkillTuple(
        skill_id=goal_id,
        description=f"Bounded primitive goal: {goal_id}",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=backends,
        preferred_backends=backends,
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
    )
