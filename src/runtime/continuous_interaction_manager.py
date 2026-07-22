"""Main runtime orchestration skeleton"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Protocol

from src.adaptation.failure_boundary import FailureAnalysis
from src.adaptation.llm_judge import LLMJudge, LLMJudgeInput, LLMJudgeOutputError, LLMJudgeUnavailable
from src.adaptation.rule_classifier import RuleFailureClassifier
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger
from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.effectors.system1_reflex_library import System1ReflexLibrary
from src.recovery.recovery_cascade import RecoveryCascade
from src.runtime.action_context import build_action_context
from src.runtime.affordance_controller import AffordanceController
from src.runtime.backend_router import RecoveryRoutingContext, RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance
from src.runtime.episode import (
    CancellationToken,
    EpisodeContext,
    EpisodePolicy,
    ObservationProvider,
    ObservationRequest,
    TransitionLedger,
    TransitionRecord,
    abstract_state_id,
    stable_affordance_key,
)
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_observation import LiveRuntimeObservation
from src.runtime.plan_validator import PlanValidator
from src.runtime.primitive_action import PrimitiveAction
from src.runtime.state_machine import RuntimeState
from src.runtime.system2_planner import System2Planner
from src.runtime.task_planner import primitive_for_affordance
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
    episode_id: str = ""
    attempts: int = 0
    transition_ids: list[str] = field(default_factory=list)
    recovery_attempted: bool = False
    recovery_succeeded: bool = False
    final_outcome_verified: bool = False
    system1_cache_hit: bool = False
    system1_fast_path: bool = False
    system1_routing_latency_ms: float = 0.0


@dataclass
class _PrimitiveOutcome:
    succeeded: bool
    observation: Observation
    result: ExecutionResult | None
    backend: str = ""
    reason: str = ""
    failure_boundary: str = ""
    failure_type: str = ""
    recovery_tier: int | None = None
    recovery_attempted: bool = False
    recovery_trace: list[dict[str, object]] = field(default_factory=list)
    transition_ids: list[str] = field(default_factory=list)
    active_perception_trace: list[dict[str, object]] = field(default_factory=list)


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
        observation_provider: ObservationProvider | None = None,
        episode_policy: EpisodePolicy | None = None,
        transition_ledger: TransitionLedger | None = None,
        failure_ledger: TraceLedger | None = None,
        reflex_library: System1ReflexLibrary | None = None,
        cancellation_token: CancellationToken | None = None,
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
        self.observation_provider = observation_provider
        self.episode_policy = episode_policy or EpisodePolicy()
        self.transition_ledger = transition_ledger or TransitionLedger()
        self.failure_ledger = failure_ledger or TraceLedger()
        self.reflex_library = reflex_library or System1ReflexLibrary()
        self._last_system1_cache_hit = False
        self._last_system1_routing_latency_ms = 0.0
        self.cancellation_token = cancellation_token or CancellationToken()

    async def run_skill(self, skill_call: SkillCall, observation: Observation) -> RuntimeStepResult:
        skill_tuple = self._lookup_skill(skill_call.skill_id)
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
        episode = EpisodeContext(
            self.cognitive_map.task_id,
            self.episode_policy,
            cancellation=self.cancellation_token,
        )
        self.cognitive_map.set_current_skill(skill_call)
        self.cognitive_map.update_from_observation(observation)

        gate = await self._run_fusion_gate(observation)
        if gate is not None:
            gate.episode_id = episode.episode_id
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
            self.state = RuntimeState.FAILED
            return RuntimeStepResult(
                self.state,
                None,
                reason="precondition failed",
                failure_boundary="recoverable_execution_failure",
                failure_type="precondition_failed",
                episode_id=episode.episode_id,
                final_outcome_verified=False,
            )

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

        return await self._execute_skill_episode(
            skill_call=skill_call,
            skill_tuple=skill_tuple,
            observation=observation,
            initial_backend=backend,
            routing_reason=routing_reason,
            episode=episode,
            active_perception_trace=active_perception_trace,
            fusion_payload=fusion_payload,
        )

    async def _execute_skill_episode(
        self,
        *,
        skill_call: SkillCall,
        skill_tuple: SkillTuple,
        observation: Observation,
        initial_backend: str,
        routing_reason: str,
        episode: EpisodeContext,
        active_perception_trace: list[dict[str, object]],
        fusion_payload: dict[str, object],
    ) -> RuntimeStepResult:
        backend = initial_backend
        current_observation = observation
        recovery_trace: list[dict[str, object]] = []
        transition_ids: list[str] = []
        recovery_attempted = False
        recovery_tier: int | None = None
        recovery_action = ""
        checkpoint_state_id = abstract_state_id(self.cognitive_map)
        last_result: ExecutionResult | None = None
        last_llm_analysis: FailureAnalysis | None = None
        last_analysis: FailureAnalysis | None = None
        pending_failure_events: list[EpisodeFailureEvent] = []

        while True:
            terminal_reason = episode.terminal_reason()
            if terminal_reason:
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=recovery_tier,
                    selected_backend=backend,
                    reason=terminal_reason,
                    routing_reason=routing_reason,
                    failure_boundary="recoverable_execution_failure",
                    failure_type="episode_budget_exhausted",
                    recovery_trace=recovery_trace,
                    llm_failure_boundary=last_llm_analysis.boundary.value if last_llm_analysis else "",
                    llm_failure_type=last_llm_analysis.failure_type if last_llm_analysis else "",
                    llm_judge_evidence=last_llm_analysis.evidence if last_llm_analysis else [],
                    active_perception_trace=active_perception_trace,
                    fusion_decision=fusion_payload,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    recovery_succeeded=False,
                    final_outcome_verified=False,
                )

            state_before = abstract_state_id(self.cognitive_map)
            try:
                attempt, transition_id = episode.begin_attempt(backend)
            except RuntimeError as exc:
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=recovery_tier,
                    selected_backend=backend,
                    reason=str(exc),
                    routing_reason=routing_reason,
                    failure_boundary="recoverable_execution_failure",
                    failure_type="episode_budget_exhausted",
                    recovery_trace=recovery_trace,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    final_outcome_verified=False,
                )

            self.state = RuntimeState.EXECUTING
            result = await self._execute_call(skill_call, backend, current_observation, skill_tuple.timeout_ms)
            result.attempt = attempt
            result.transition_id = transition_id
            self.cognitive_map.record_execution_result(result)
            last_result = result

            current_observation, observation_failure = await self._refresh_observation(
                episode,
                result,
                current_observation,
            )
            gate = await self._run_fusion_gate(current_observation)
            fusion_payload = self._last_fusion_decision
            active_perception_trace.extend(self._last_active_perception_trace)
            if gate is not None:
                self._record_transition(
                    episode,
                    transition_id,
                    state_before,
                    skill_call,
                    result,
                    postcondition_passed=False,
                    recovery_action="escalate_human",
                    recovery_tier=4,
                )
                transition_ids.append(transition_id)
                gate.execution_result = result
                gate.episode_id = episode.episode_id
                gate.attempts = episode.step_count
                gate.transition_ids = transition_ids
                gate.recovery_attempted = recovery_attempted
                gate.final_outcome_verified = False
                return gate

            self.state = RuntimeState.VERIFYING
            postcondition_passed = (
                result.success
                and observation_failure is None
                and self.postconditions.passes(
                    skill_tuple.postconditions,
                    self.cognitive_map,
                )
            )
            if postcondition_passed:
                self._remember_reflex(skill_call.skill_id, backend)
                for event in pending_failure_events:
                    event.recovery_success = True
                self._record_transition(
                    episode,
                    transition_id,
                    state_before,
                    skill_call,
                    result,
                    postcondition_passed=True,
                    recovery_action=recovery_action,
                    recovery_tier=recovery_tier,
                )
                transition_ids.append(transition_id)
                self.state = RuntimeState.COMPLETED
                return RuntimeStepResult(
                    self.state,
                    result,
                    recovery_tier=recovery_tier,
                    selected_backend=backend,
                    reason="skill completed after recovery" if recovery_attempted else "skill completed",
                    routing_reason=routing_reason,
                    failure_boundary=last_analysis.boundary.value if last_analysis else "",
                    failure_type=last_analysis.failure_type if last_analysis else "",
                    recovery_trace=recovery_trace,
                    llm_failure_boundary=last_llm_analysis.boundary.value if last_llm_analysis else "",
                    llm_failure_type=last_llm_analysis.failure_type if last_llm_analysis else "",
                    llm_judge_evidence=last_llm_analysis.evidence if last_llm_analysis else [],
                    active_perception_trace=active_perception_trace,
                    fusion_decision=fusion_payload,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    recovery_succeeded=recovery_attempted,
                    final_outcome_verified=True,
                    system1_cache_hit=self._last_system1_cache_hit,
                    system1_fast_path=self._last_system1_cache_hit,
                    system1_routing_latency_ms=self._last_system1_routing_latency_ms,
                )

            failure = _verification_failure(result, observation_failure)
            self.reflex_library.forget(skill_call.skill_id, backend)
            analysis = self.failure_classifier.classify_execution_failure(failure, skill_tuple, self.cognitive_map)
            last_analysis = analysis
            llm_analysis = self._advisory_llm_analysis(failure, skill_tuple, analysis, episode.tried_backends)
            last_llm_analysis = llm_analysis or last_llm_analysis
            rollback_available = self._rollback_is_executable(skill_tuple)
            action, trace = self.recovery_cascade.select_with_trace(
                failure,
                skill_tuple,
                self.cognitive_map,
                available_backends=list(self.executors),
                retry_count=episode.retry_count,
                tried_backends=episode.tried_backends,
                rollback_available=rollback_available,
                boundary=analysis.boundary.value,
                max_retry_attempts=episode.policy.max_retry_attempts,
            )
            recovery_attempted = True
            recovery_tier = trace.selected_tier
            recovery_action = trace.selected_action
            recovery_trace.extend(
                {**asdict(step), "attempt": episode.step_count, "selected_action": trace.selected_action}
                for step in trace.steps
            )
            self._record_transition(
                episode,
                transition_id,
                state_before,
                skill_call,
                result,
                postcondition_passed=False,
                recovery_action=trace.selected_action,
                recovery_tier=trace.selected_tier,
                verification_failure_reason=failure.failure_reason or "",
            )
            transition_ids.append(transition_id)
            pending_failure_events.append(
                self._record_failure_event(
                    episode,
                    skill_call,
                    failure,
                    analysis,
                    state_before=state_before,
                    transition_id=transition_id,
                    recovery_action=trace.selected_action,
                )
            )

            if action.action_type == "retry":
                episode.retry_count += 1
                self.state = RuntimeState.RECOVERING
                if action.delay_s > 0:
                    await asyncio.sleep(action.delay_s)
                continue

            if action.action_type == "reroute":
                decision = self.backend_router.select_backend(
                    skill_call,
                    self.cognitive_map,
                    RecoveryRoutingContext(
                        exclude_backends=list(episode.tried_backends),
                        previous_failures=dict(episode.backend_attempts),
                    ),
                )
                candidate = decision.backend or action.backend
                if candidate in self.executors and candidate in skill_tuple.allowed_backends:
                    backend = candidate
                    routing_reason = decision.reason or action.reason
                    self.state = RuntimeState.RECOVERING
                    continue
                action.action_type = "escalate_human"
                action.reason = "reroute selected no executable alternative backend"

            if action.action_type == "rollback":
                rollback_succeeded, rollback_result, rollback_transition_id = await self._execute_rollback(
                    action.rollback_call,
                    original_call=skill_call,
                    observation=current_observation,
                    episode=episode,
                    checkpoint_state_id=checkpoint_state_id,
                )
                if rollback_transition_id:
                    transition_ids.append(rollback_transition_id)
                pending_failure_events[-1].recovery_success = rollback_succeeded
                self.state = RuntimeState.FAILED if rollback_succeeded else RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    rollback_result or result,
                    recovery_tier=3,
                    selected_backend=(rollback_result.backend_used if rollback_result else backend),
                    reason="rollback executed and verified" if rollback_succeeded else "rollback execution failed",
                    routing_reason=routing_reason,
                    failure_boundary=analysis.boundary.value,
                    failure_type=analysis.failure_type,
                    recovery_trace=recovery_trace,
                    llm_failure_boundary=llm_analysis.boundary.value if llm_analysis else "",
                    llm_failure_type=llm_analysis.failure_type if llm_analysis else "",
                    llm_judge_evidence=llm_analysis.evidence if llm_analysis else [],
                    fusion_decision=self._last_fusion_decision,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=True,
                    recovery_succeeded=rollback_succeeded,
                    final_outcome_verified=False,
                )

            self.state = RuntimeState.ESCALATED if action.action_type == "escalate_human" else RuntimeState.FAILED
            return RuntimeStepResult(
                self.state,
                result,
                recovery_tier=trace.selected_tier,
                selected_backend=backend,
                reason=action.reason,
                routing_reason=routing_reason,
                failure_boundary=analysis.boundary.value,
                failure_type=analysis.failure_type,
                recovery_trace=recovery_trace,
                llm_failure_boundary=llm_analysis.boundary.value if llm_analysis else "",
                llm_failure_type=llm_analysis.failure_type if llm_analysis else "",
                llm_judge_evidence=llm_analysis.evidence if llm_analysis else [],
                active_perception_trace=active_perception_trace,
                fusion_decision=fusion_payload,
                episode_id=episode.episode_id,
                attempts=episode.step_count,
                transition_ids=transition_ids,
                recovery_attempted=True,
                recovery_succeeded=False,
                final_outcome_verified=False,
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
        goal_call = SkillCall(goal_id, dict(parameters or {}))
        episode = EpisodeContext(
            self.cognitive_map.task_id,
            self.episode_policy,
            cancellation=self.cancellation_token,
        )
        self.cognitive_map.set_current_skill(goal_call)
        self.cognitive_map.update_from_observation(observation)
        gate = await self._run_fusion_gate(observation)
        if gate is not None:
            gate.episode_id = episode.episode_id
            return gate

        safety_constraints = ["do not use raw selectors", "do not bypass unresolved sensory conflicts"]
        if goal_spec is not None:
            safety_constraints.extend(goal_spec.safety_constraints)
        return await self._execute_goal_episode(
            goal_call=goal_call,
            goal_state=goal_state,
            parameters=dict(parameters or {}),
            observation=observation,
            safety_constraints=safety_constraints,
            episode=episode,
        )

    async def _execute_goal_episode(
        self,
        *,
        goal_call: SkillCall,
        goal_state: str,
        parameters: dict[str, object],
        observation: Observation,
        safety_constraints: list[str],
        episode: EpisodeContext,
    ) -> RuntimeStepResult:
        current_observation = observation
        completed_steps: set[tuple[str, str, str]] = set()
        primitive_plan: list[dict[str, object]] = []
        recovery_trace: list[dict[str, object]] = []
        transition_ids: list[str] = []
        active_perception_trace = list(self._last_active_perception_trace)
        last_result: ExecutionResult | None = None
        selected_backend = ""
        recovery_attempted = False
        recovery_tier: int | None = None

        while True:
            if goal_state and self.postconditions.passes([Condition(goal_state)], self.cognitive_map):
                self.state = RuntimeState.COMPLETED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=recovery_tier,
                    selected_backend=selected_backend,
                    reason="goal completed",
                    routing_reason="observe-plan-act-reobserve loop",
                    recovery_trace=recovery_trace,
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=active_perception_trace,
                    primitive_plan=primitive_plan,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    recovery_succeeded=recovery_attempted,
                    final_outcome_verified=True,
                )

            terminal_reason = episode.terminal_reason()
            if terminal_reason:
                return self._episode_failure_result(
                    episode,
                    last_result,
                    terminal_reason,
                    "episode_budget_exhausted",
                    selected_backend,
                    primitive_plan,
                    recovery_trace,
                    transition_ids,
                    recovery_attempted,
                )

            context = build_action_context(
                self.cognitive_map,
                request_type="goal_spec",
                safety_constraints=safety_constraints,
            )
            plan = self.system2_planner.plan(
                context,
                goal_id=goal_call.skill_id,
                goal_state=goal_state,
                parameters=parameters,
            )
            validation = self.plan_validator.validate(context, plan.actions)
            if plan.requires_escalation or not validation.valid:
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=4,
                    reason=plan.reason or "primitive plan failed validation",
                    failure_boundary="skill_spec_insufficient",
                    failure_type="insufficient_affordance_plan",
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=active_perception_trace,
                    primitive_plan=[*primitive_plan, *[_primitive_payload(action) for action in plan.actions]],
                    plan_validation_errors=validation.errors,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    final_outcome_verified=False,
                )

            action = next(
                (
                    candidate
                    for candidate in plan.actions
                    if candidate.action not in {"done", "wait", "ask_user"}
                    and _primitive_signature(candidate) not in completed_steps
                ),
                None,
            )
            if action is None:
                clarification = next((candidate for candidate in plan.actions if candidate.action == "ask_user"), None)
                reason = (
                    clarification.expected_effect
                    if clarification is not None
                    else "planner produced no new action while goal remains unverified"
                )
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=4,
                    reason=reason,
                    failure_boundary="skill_spec_insufficient",
                    failure_type="clarification_required" if clarification else "planner_stalled",
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=active_perception_trace,
                    primitive_plan=primitive_plan,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    final_outcome_verified=False,
                )

            primitive_plan.append(_primitive_payload(action))
            outcome = await self._execute_primitive_with_recovery(
                action=action,
                goal_call=goal_call,
                parameters=parameters,
                observation=current_observation,
                episode=episode,
            )
            current_observation = outcome.observation
            last_result = outcome.result
            selected_backend = outcome.backend
            recovery_attempted = recovery_attempted or outcome.recovery_attempted
            recovery_tier = outcome.recovery_tier or recovery_tier
            recovery_trace.extend(outcome.recovery_trace)
            transition_ids.extend(outcome.transition_ids)
            active_perception_trace.extend(outcome.active_perception_trace)
            if not outcome.succeeded:
                self.state = RuntimeState.ESCALATED
                return RuntimeStepResult(
                    self.state,
                    last_result,
                    recovery_tier=recovery_tier or 4,
                    selected_backend=selected_backend,
                    reason=outcome.reason,
                    failure_boundary=outcome.failure_boundary,
                    failure_type=outcome.failure_type,
                    recovery_trace=recovery_trace,
                    fusion_decision=self._last_fusion_decision,
                    active_perception_trace=active_perception_trace,
                    primitive_plan=primitive_plan,
                    episode_id=episode.episode_id,
                    attempts=episode.step_count,
                    transition_ids=transition_ids,
                    recovery_attempted=recovery_attempted,
                    recovery_succeeded=False,
                    final_outcome_verified=False,
                )
            completed_steps.add(_primitive_signature(action))

    async def _execute_primitive_with_recovery(
        self,
        *,
        action: PrimitiveAction,
        goal_call: SkillCall,
        parameters: dict[str, object],
        observation: Observation,
        episode: EpisodeContext,
    ) -> _PrimitiveOutcome:
        current_action = action
        current_observation = observation
        tried_affordances: set[str] = set()
        recovery_trace: list[dict[str, object]] = []
        transition_ids: list[str] = []
        active_trace: list[dict[str, object]] = []
        pending_failure_events: list[EpisodeFailureEvent] = []
        recovery_attempted = False
        recovery_tier: int | None = None
        selected_recovery_action = ""

        while True:
            affordance = self.cognitive_map.runtime_affordances.get(current_action.affordance_id)
            if affordance is None:
                return _PrimitiveOutcome(
                    False,
                    current_observation,
                    None,
                    reason=f"affordance disappeared before execution: {current_action.affordance_id}",
                    failure_boundary="recoverable_execution_failure",
                    failure_type="stale_affordance",
                    recovery_tier=recovery_tier,
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )
            backend = affordance.source
            if backend not in self.executors:
                return _PrimitiveOutcome(
                    False,
                    current_observation,
                    None,
                    backend=backend,
                    reason="no executor for affordance backend",
                    failure_boundary="architecture_gap",
                    failure_type="no_executor_for_affordance_backend",
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )

            primitive_skill = _primitive_skill_tuple(
                goal_call.skill_id,
                list(self.executors),
                idempotent=(
                    current_action.action in {"type", "select", "read"} or bool(affordance.grounding.get("idempotent"))
                ),
            )
            primitive_call = SkillCall(
                skill_id=goal_call.skill_id,
                params={
                    "primitive_action": current_action.action,
                    "affordance_id": current_action.affordance_id,
                    "value": current_action.value,
                    "expected_effect": current_action.expected_effect,
                    **parameters,
                },
            )
            safety = self.safety.decide(primitive_call, primitive_skill)
            if not safety.allowed:
                return _PrimitiveOutcome(
                    False,
                    current_observation,
                    None,
                    backend=backend,
                    reason=safety.reason,
                    failure_boundary="unsafe_governance_boundary",
                    failure_type="unsafe_primitive_action",
                    recovery_tier=4,
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )

            state_before = abstract_state_id(self.cognitive_map)
            try:
                attempt, transition_id = episode.begin_attempt(backend)
            except RuntimeError as exc:
                return _PrimitiveOutcome(
                    False,
                    current_observation,
                    None,
                    backend=backend,
                    reason=str(exc),
                    failure_boundary="recoverable_execution_failure",
                    failure_type="episode_budget_exhausted",
                    recovery_tier=recovery_tier,
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )

            self.state = RuntimeState.EXECUTING
            result = await self._execute_call(primitive_call, backend, current_observation, primitive_skill.timeout_ms)
            result.attempt = attempt
            result.transition_id = transition_id
            self.cognitive_map.record_execution_result(result)
            tried_affordances.add(current_action.affordance_id)
            current_observation, observation_failure = await self._refresh_observation(
                episode,
                result,
                current_observation,
            )
            gate = await self._run_fusion_gate(current_observation)
            active_trace.extend(self._last_active_perception_trace)
            if gate is not None:
                self._record_transition(
                    episode,
                    transition_id,
                    state_before,
                    primitive_call,
                    result,
                    postcondition_passed=False,
                    recovery_action="escalate_human",
                    recovery_tier=4,
                    affordance_key=stable_affordance_key(self.cognitive_map, current_action.affordance_id),
                )
                transition_ids.append(transition_id)
                return _PrimitiveOutcome(
                    False,
                    current_observation,
                    result,
                    backend=backend,
                    reason=gate.reason,
                    failure_boundary=gate.failure_boundary,
                    failure_type=gate.failure_type,
                    recovery_tier=4,
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )

            if result.success and observation_failure is None:
                for event in pending_failure_events:
                    event.recovery_success = True
                self._record_transition(
                    episode,
                    transition_id,
                    state_before,
                    primitive_call,
                    result,
                    postcondition_passed=True,
                    recovery_action=selected_recovery_action,
                    recovery_tier=recovery_tier,
                    affordance_key=stable_affordance_key(self.cognitive_map, current_action.affordance_id),
                )
                transition_ids.append(transition_id)
                return _PrimitiveOutcome(
                    True,
                    current_observation,
                    result,
                    backend=backend,
                    recovery_tier=recovery_tier,
                    recovery_attempted=recovery_attempted,
                    recovery_trace=recovery_trace,
                    transition_ids=transition_ids,
                    active_perception_trace=active_trace,
                )

            failure = _verification_failure(result, observation_failure)
            analysis = self.failure_classifier.classify_execution_failure(
                failure,
                primitive_skill,
                self.cognitive_map,
            )
            recovery_action, trace = self.recovery_cascade.select_with_trace(
                failure,
                primitive_skill,
                self.cognitive_map,
                available_backends=list(self.executors),
                retry_count=episode.retry_count,
                tried_backends=episode.tried_backends,
                rollback_available=False,
                boundary=analysis.boundary.value,
                max_retry_attempts=episode.policy.max_retry_attempts,
            )
            recovery_attempted = True
            recovery_tier = trace.selected_tier
            selected_recovery_action = trace.selected_action
            recovery_trace.extend(
                {**asdict(step), "attempt": episode.step_count, "selected_action": trace.selected_action}
                for step in trace.steps
            )
            self._record_transition(
                episode,
                transition_id,
                state_before,
                primitive_call,
                result,
                postcondition_passed=False,
                recovery_action=trace.selected_action,
                recovery_tier=trace.selected_tier,
                verification_failure_reason=failure.failure_reason or "",
                affordance_key=stable_affordance_key(self.cognitive_map, current_action.affordance_id),
            )
            transition_ids.append(transition_id)
            pending_failure_events.append(
                self._record_failure_event(
                    episode,
                    primitive_call,
                    failure,
                    analysis,
                    state_before=state_before,
                    transition_id=transition_id,
                    recovery_action=trace.selected_action,
                    affordance_key=stable_affordance_key(self.cognitive_map, current_action.affordance_id),
                )
            )

            if recovery_action.action_type == "retry":
                episode.retry_count += 1
                self.state = RuntimeState.RECOVERING
                if recovery_action.delay_s:
                    await asyncio.sleep(recovery_action.delay_s)
                continue
            if recovery_action.action_type == "reroute":
                alternative = _alternative_affordance(
                    self.cognitive_map,
                    current_action,
                    original_affordance=affordance,
                    excluded_ids=tried_affordances,
                    preferred_backend=recovery_action.backend,
                )
                if alternative is not None:
                    current_action = PrimitiveAction(
                        current_action.action,
                        affordance_id=alternative.id,
                        value=current_action.value,
                        expected_effect=current_action.expected_effect,
                    )
                    self.state = RuntimeState.RECOVERING
                    continue

            return _PrimitiveOutcome(
                False,
                current_observation,
                result,
                backend=backend,
                reason=recovery_action.reason,
                failure_boundary=analysis.boundary.value,
                failure_type=analysis.failure_type,
                recovery_tier=trace.selected_tier,
                recovery_attempted=True,
                recovery_trace=recovery_trace,
                transition_ids=transition_ids,
                active_perception_trace=active_trace,
            )

    def _episode_failure_result(
        self,
        episode: EpisodeContext,
        result: ExecutionResult | None,
        reason: str,
        failure_type: str,
        backend: str,
        primitive_plan: list[dict[str, object]],
        recovery_trace: list[dict[str, object]],
        transition_ids: list[str],
        recovery_attempted: bool,
    ) -> RuntimeStepResult:
        self.state = RuntimeState.ESCALATED
        return RuntimeStepResult(
            self.state,
            result,
            selected_backend=backend,
            reason=reason,
            failure_boundary="recoverable_execution_failure",
            failure_type=failure_type,
            recovery_trace=recovery_trace,
            fusion_decision=self._last_fusion_decision,
            primitive_plan=primitive_plan,
            episode_id=episode.episode_id,
            attempts=episode.step_count,
            transition_ids=transition_ids,
            recovery_attempted=recovery_attempted,
            recovery_succeeded=False,
            final_outcome_verified=False,
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

        observation = live_observation.apply_affordances_to(self.cognitive_map)
        return await self.run_goal(
            goal_id=goal_id,
            goal_state=goal_state,
            parameters=parameters,
            observation=observation,
            goal_spec=goal_spec,
        )

    async def _execute_call(
        self,
        skill_call: SkillCall,
        backend: str,
        observation: Observation,
        timeout_ms: int,
    ) -> ExecutionResult:
        try:
            return await asyncio.wait_for(
                self.executors[backend].execute(skill_call, observation),
                timeout=max(timeout_ms, 1) / 1000,
            )
        except TimeoutError:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=backend,
                success=False,
                latency_ms=float(timeout_ms),
                confidence=0.0,
                failure_reason="timeout",
            )
        except Exception as exc:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=backend,
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"executor_exception:{type(exc).__name__}",
                metadata={"exception_message": str(exc)},
            )

    async def _refresh_observation(
        self,
        episode: EpisodeContext,
        result: ExecutionResult,
        current: Observation,
    ) -> tuple[Observation, str | None]:
        if self.observation_provider is None:
            if self.episode_policy.require_fresh_observation:
                return current, "fresh_observation_unavailable"
            return current, None
        request = ObservationRequest(
            task_id=self.cognitive_map.task_id,
            episode_id=episode.episode_id,
            reason="post_action_verification",
            step=episode.step_count,
            previous_result=result,
        )
        try:
            observed = await self.observation_provider.observe(request)
        except Exception as exc:
            return current, f"observation_provider_error:{type(exc).__name__}"
        if isinstance(observed, LiveRuntimeObservation):
            fresh = observed.apply_to(self.cognitive_map)
        else:
            fresh = observed
            self.cognitive_map.update_from_observation(fresh)
        return fresh, None

    def _record_transition(
        self,
        episode: EpisodeContext,
        transition_id: str,
        state_before: str,
        skill_call: SkillCall,
        result: ExecutionResult,
        *,
        postcondition_passed: bool | None,
        recovery_action: str = "",
        recovery_tier: int | None = None,
        reversible_result: bool | None = None,
        affordance_key: str = "",
        verification_failure_reason: str = "",
    ) -> None:
        self.transition_ledger.record(
            TransitionRecord(
                task_id=self.cognitive_map.task_id,
                episode_id=episode.episode_id,
                transition_id=transition_id,
                step=episode.step_count,
                state_id_before=state_before,
                state_id_after=abstract_state_id(self.cognitive_map),
                skill_id=skill_call.skill_id,
                affordance_key=affordance_key,
                backend=result.backend_used,
                params=dict(skill_call.params),
                success=bool(result.success and postcondition_passed),
                execution_success=result.success,
                postcondition_passed=postcondition_passed,
                latency_ms=result.latency_ms,
                attempt=result.attempt,
                observation_delta=dict(result.raw_observation_delta),
                recovery_action=recovery_action,
                recovery_tier=recovery_tier,
                failure_reason=verification_failure_reason or result.failure_reason or "",
                reversible_result=reversible_result,
            )
        )

    def _record_failure_event(
        self,
        episode: EpisodeContext,
        skill_call: SkillCall,
        result: ExecutionResult,
        analysis: FailureAnalysis,
        *,
        state_before: str,
        transition_id: str,
        recovery_action: str,
        affordance_key: str = "",
    ) -> EpisodeFailureEvent:
        transition = next(
            (record for record in reversed(self.transition_ledger.records) if record.transition_id == transition_id),
            None,
        )
        event = EpisodeFailureEvent(
            episode_id=episode.episode_id,
            task_id=self.cognitive_map.task_id,
            skill_id=skill_call.skill_id,
            backend=result.backend_used,
            failure_type=analysis.failure_type,
            boundary=analysis.boundary.value,
            context_key=self.cognitive_map.task_id,
            incident_id=str(result.metadata.get("incident_id", "")),
            recovery_action=recovery_action,
            recovery_success=False,
            transition_id=transition_id,
            state_id_before=state_before,
            state_id_after=(
                transition.state_id_after if transition is not None else abstract_state_id(self.cognitive_map)
            ),
            affordance_key=affordance_key,
        )
        self.failure_ledger.record(event)
        return event

    def _rollback_is_executable(self, skill_tuple: SkillTuple) -> bool:
        if skill_tuple.rollback is None or skill_tuple.irreversible:
            return False
        rollback_skill = self._lookup_skill(skill_tuple.rollback.skill_id)
        if rollback_skill is None:
            return False
        return any(backend in self.executors for backend in rollback_skill.allowed_backends)

    async def _execute_rollback(
        self,
        rollback_call: SkillCall | None,
        *,
        original_call: SkillCall,
        observation: Observation,
        episode: EpisodeContext,
        checkpoint_state_id: str,
    ) -> tuple[bool, ExecutionResult | None, str]:
        if rollback_call is None:
            return False, None, ""
        rollback_skill = self._lookup_skill(rollback_call.skill_id)
        if rollback_skill is None:
            return False, None, ""
        self.cognitive_map.set_current_skill(rollback_call)
        backend, _ = self._select_backend(rollback_call, rollback_skill)
        if not backend:
            self.cognitive_map.set_current_skill(original_call)
            return False, None, ""
        state_before = abstract_state_id(self.cognitive_map)
        try:
            attempt, transition_id = episode.begin_attempt(backend)
        except RuntimeError:
            self.cognitive_map.set_current_skill(original_call)
            return False, None, ""
        self.state = RuntimeState.RECOVERING
        result = await self._execute_call(rollback_call, backend, observation, rollback_skill.timeout_ms)
        result.attempt = attempt
        result.transition_id = transition_id
        self.cognitive_map.record_execution_result(result)
        fresh, observation_failure = await self._refresh_observation(episode, result, observation)
        gate = await self._run_fusion_gate(fresh)
        postcondition_passed = (
            result.success
            and observation_failure is None
            and gate is None
            and self.postconditions.passes(rollback_skill.postconditions, self.cognitive_map)
        )
        restored_state = abstract_state_id(self.cognitive_map) == checkpoint_state_id
        verified = postcondition_passed and (bool(rollback_skill.postconditions) or restored_state)
        self._record_transition(
            episode,
            transition_id,
            state_before,
            rollback_call,
            result,
            postcondition_passed=postcondition_passed,
            recovery_action="rollback",
            recovery_tier=3,
            reversible_result=verified,
        )
        self.cognitive_map.set_current_skill(original_call)
        return verified, result, transition_id

    def _lookup_skill(self, skill_id: str) -> SkillTuple | None:
        try:
            return self.skill_library.get(skill_id)
        except (KeyError, ValueError):
            return None

    def _select_backend(self, skill_call: SkillCall, skill_tuple: SkillTuple) -> tuple[str, str]:
        started = time.perf_counter()
        self._last_system1_cache_hit = False
        cached = self.reflex_library.recall(skill_call.skill_id)
        if cached is not None:
            current = next(
                (affordance for affordance in self.cognitive_map.affordances if affordance.id == cached.id), None
            )
            backend = cached.source.lower()
            if (
                current is not None
                and self.reflex_library.is_reflex(current)
                and backend in skill_tuple.allowed_backends
                and backend in self.executors
            ):
                self._last_system1_cache_hit = True
                self._last_system1_routing_latency_ms = (time.perf_counter() - started) * 1000
                return backend, f"system1 cached grounding {cached.id}"

        routing_decision = self.backend_router.select_backend(skill_call, self.cognitive_map)
        if (
            routing_decision.backend
            and routing_decision.backend in skill_tuple.allowed_backends
            and routing_decision.backend in self.executors
        ):
            self._last_system1_routing_latency_ms = (time.perf_counter() - started) * 1000
            return routing_decision.backend, routing_decision.reason

        preferences = skill_call.preferred_backends or skill_tuple.preferred_backends
        for backend in preferences:
            if backend in skill_tuple.allowed_backends and backend in self.executors:
                self._last_system1_routing_latency_ms = (time.perf_counter() - started) * 1000
                return backend, f"fallback preferred backend {backend}"
        for backend in skill_tuple.allowed_backends:
            if backend in self.executors:
                self._last_system1_routing_latency_ms = (time.perf_counter() - started) * 1000
                return backend, f"fallback allowed backend {backend}"
        self._last_system1_routing_latency_ms = (time.perf_counter() - started) * 1000
        if routing_decision.reason:
            return "", routing_decision.reason
        return "", "no allowed executor backend available"

    def _remember_reflex(self, skill_id: str, backend: str) -> None:
        candidate = next(
            (
                affordance
                for affordance in self.cognitive_map.affordances
                if affordance.source.lower() == backend
                and (
                    affordance.locator.get("skill_id") == skill_id
                    or affordance.state.get("skill_id") == skill_id
                    or affordance.action == skill_id
                )
            ),
            None,
        )
        if candidate is not None and self.reflex_library.is_reflex(candidate):
            self.reflex_library.remember(skill_id, candidate)

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


def _primitive_signature(action: PrimitiveAction) -> tuple[str, str, str]:
    return (action.action, str(action.value), action.expected_effect)


def _alternative_affordance(
    cognitive_map: CognitiveMap,
    action: PrimitiveAction,
    *,
    original_affordance: RuntimeAffordance,
    excluded_ids: set[str],
    preferred_backend: str,
) -> RuntimeAffordance | None:
    semantic_keys = {
        "parameter",
        "binds_parameter",
        "completion_for",
        "goal_id",
        "achieves",
        "effects",
    }
    original_semantics = {
        (key, str(original_affordance.grounding[key])) for key in semantic_keys if key in original_affordance.grounding
    }
    candidates: list[RuntimeAffordance] = []
    for affordance in cognitive_map.runtime_affordances.values():
        if affordance.id in excluded_ids or primitive_for_affordance(affordance) != action.action:
            continue
        candidate_semantics = {
            (key, str(affordance.grounding[key])) for key in semantic_keys if key in affordance.grounding
        }
        if original_semantics:
            if not original_semantics.intersection(candidate_semantics):
                continue
        elif (
            affordance.entity_id != original_affordance.entity_id
            and affordance.action_name != original_affordance.action_name
        ):
            continue
        candidates.append(affordance)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda affordance: (
            affordance.source == preferred_backend,
            affordance.confidence,
        ),
    )


def _verification_failure(result: ExecutionResult, observation_failure: str | None) -> ExecutionResult:
    if not result.success:
        return result
    return ExecutionResult(
        skill_id=result.skill_id,
        backend_used=result.backend_used,
        success=False,
        latency_ms=result.latency_ms,
        confidence=result.confidence,
        failure_reason=observation_failure or "postcondition_failed",
        observation_source=result.observation_source,
        attempt=result.attempt,
        transition_id=result.transition_id,
        metadata=dict(result.metadata),
    )


def _primitive_skill_tuple(
    goal_id: str,
    allowed_backends: list[str],
    *,
    idempotent: bool = False,
) -> SkillTuple:
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
        idempotent=idempotent,
    )
