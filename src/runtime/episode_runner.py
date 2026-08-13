"""Shared entry point for running environment episodes through CIM.

This module keeps demo and benchmark glue thin: environments provide reset,
observation, and executors; the runner owns the uniform runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from evaluation.metrics_aggregator import MetricReport, aggregate_metrics, dataset_from_runtime_results
from src.adaptation.llm_judge import LLMJudge
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Observation, SkillCall, SkillTuple
from src.recovery.recovery_cascade import RecoveryCascade
from src.runtime.backend_router import RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, Executor, RuntimeStepResult
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_observation import LiveRuntimeObservation, bind_live_observation_to_request
from src.runtime.plan_validator import PlanValidator
from src.runtime.system2_planner import System2Planner
from src.verification.active_perception import ActivePerceptionResolver
from src.verification.conflict_detector import EpistemicArbiter


@dataclass(frozen=True)
class RuntimeEpisodeSpec:
    task_id: str
    goal_id: str = ""
    goal_state: str = ""
    parameters: dict[str, object] = field(default_factory=dict)
    goal_spec: GoalSpec | None = None
    data_source: str = "runtime_episode"


@dataclass
class RuntimeEpisodeOutcome:
    result: RuntimeStepResult
    cognitive_map: CognitiveMap
    transition_ledger: TransitionLedger
    failure_ledger: TraceLedger
    metrics: MetricReport


class RuntimeEnvironmentAdapter(Protocol):
    async def reset(self, spec: RuntimeEpisodeSpec) -> None: ...

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation | Observation: ...

    def executors(self) -> dict[str, Executor]: ...


class StaticRuntimeEnvironmentAdapter:
    """In-memory adapter for deterministic tests and white-box demos."""

    def __init__(
        self,
        executors: dict[str, Executor],
        observations: list[LiveRuntimeObservation | Observation] | None = None,
    ) -> None:
        self._executors = executors
        self._observations = list(observations or [Observation()])
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation | Observation:
        self.requests.append(request)
        if len(self._observations) > 1:
            observed = self._observations.pop(0)
        else:
            observed = self._observations[0]
        if isinstance(observed, LiveRuntimeObservation):
            return bind_live_observation_to_request(observed, request_id=request.request_id)
        return observed

    def executors(self) -> dict[str, Executor]:
        return self._executors


class RuntimeEpisodeRunner:
    """Run a structured goal episode through the canonical runtime loop."""

    def __init__(
        self,
        *,
        skill_library: dict[str, SkillTuple] | None = None,
        episode_policy: EpisodePolicy | None = None,
        transition_ledger: TransitionLedger | None = None,
        failure_ledger: TraceLedger | None = None,
        backend_router: RuntimeBackendRouter | None = None,
        epistemic_arbiter: EpistemicArbiter | None = None,
        recovery_cascade: RecoveryCascade | None = None,
        llm_judge: LLMJudge | None = None,
        use_llm_judge: bool = False,
        active_perception_resolver: ActivePerceptionResolver | None = None,
        system2_planner: System2Planner | None = None,
        plan_validator: PlanValidator | None = None,
    ) -> None:
        self.skill_library = dict(skill_library or {})
        self.episode_policy = episode_policy
        self.transition_ledger = transition_ledger or TransitionLedger()
        self.failure_ledger = failure_ledger or TraceLedger()
        self.backend_router = backend_router
        self.epistemic_arbiter = epistemic_arbiter
        self.recovery_cascade = recovery_cascade
        self.llm_judge = llm_judge
        self.use_llm_judge = use_llm_judge
        self.active_perception_resolver = active_perception_resolver
        self.system2_planner = system2_planner
        self.plan_validator = plan_validator

    async def run_goal_episode(
        self,
        adapter: RuntimeEnvironmentAdapter,
        spec: RuntimeEpisodeSpec,
    ) -> RuntimeEpisodeOutcome:
        await adapter.reset(spec)
        cognitive_map = CognitiveMap(task_id=spec.task_id)
        manager = ContinuousInteractionManager(
            self.skill_library,
            adapter.executors(),
            cognitive_map,
            backend_router=self.backend_router,
            epistemic_arbiter=self.epistemic_arbiter,
            recovery_cascade=self.recovery_cascade,
            llm_judge=self.llm_judge,
            use_llm_judge=self.use_llm_judge,
            active_perception_resolver=self.active_perception_resolver,
            system2_planner=self.system2_planner,
            plan_validator=self.plan_validator,
            observation_provider=adapter,
            episode_policy=self.episode_policy,
            transition_ledger=self.transition_ledger,
            failure_ledger=self.failure_ledger,
        )
        initial = await adapter.observe(
            ObservationRequest(
                task_id=spec.task_id,
                episode_id="",
                reason="initial_observation",
                step=0,
            )
        )
        if isinstance(initial, LiveRuntimeObservation):
            result = await manager.run_observed_goal(
                initial,
                goal_id=spec.goal_id,
                goal_state=spec.goal_state,
                parameters=spec.parameters,
                goal_spec=spec.goal_spec,
            )
        else:
            result = await manager.run_goal(
                goal_id=spec.goal_id,
                goal_state=spec.goal_state,
                parameters=spec.parameters,
                observation=initial,
                goal_spec=spec.goal_spec,
            )
        metrics = aggregate_metrics(
            dataset_from_runtime_results([result], self.transition_ledger),
            data_source=spec.data_source,
            episode_ids=[result.episode_id],
        )
        return RuntimeEpisodeOutcome(
            result=result,
            cognitive_map=cognitive_map,
            transition_ledger=self.transition_ledger,
            failure_ledger=self.failure_ledger,
            metrics=metrics,
        )

    async def run_skill_episode(
        self,
        adapter: RuntimeEnvironmentAdapter,
        skill_call: SkillCall,
        spec: RuntimeEpisodeSpec,
    ) -> RuntimeEpisodeOutcome:
        await adapter.reset(spec)
        cognitive_map = CognitiveMap(task_id=spec.task_id)
        manager = ContinuousInteractionManager(
            self.skill_library,
            adapter.executors(),
            cognitive_map,
            backend_router=self.backend_router,
            epistemic_arbiter=self.epistemic_arbiter,
            recovery_cascade=self.recovery_cascade,
            llm_judge=self.llm_judge,
            use_llm_judge=self.use_llm_judge,
            active_perception_resolver=self.active_perception_resolver,
            system2_planner=self.system2_planner,
            plan_validator=self.plan_validator,
            observation_provider=adapter,
            episode_policy=self.episode_policy,
            transition_ledger=self.transition_ledger,
            failure_ledger=self.failure_ledger,
        )
        initial = await adapter.observe(
            ObservationRequest(
                task_id=spec.task_id,
                episode_id="",
                reason="initial_observation",
                step=0,
            )
        )
        observation = initial.apply_to(cognitive_map) if isinstance(initial, LiveRuntimeObservation) else initial
        result = await manager.run_skill(skill_call, observation)
        metrics = aggregate_metrics(
            dataset_from_runtime_results([result], self.transition_ledger),
            data_source=spec.data_source,
            episode_ids=[result.episode_id],
        )
        return RuntimeEpisodeOutcome(
            result=result,
            cognitive_map=cognitive_map,
            transition_ledger=self.transition_ledger,
            failure_ledger=self.failure_ledger,
            metrics=metrics,
        )
