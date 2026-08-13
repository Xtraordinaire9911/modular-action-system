"""Runtime episode adapter for external web benchmark pages."""

from __future__ import annotations

import time
from typing import Any

from src.benchmarks.task_spec import BenchmarkTask
from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.runtime.episode import ObservationRequest
from src.runtime.episode_runner import RuntimeEpisodeSpec
from src.runtime.live_observation import LiveRuntimeObservation, observation_from_live_sources


class RuntimeWebEnvironmentAdapter:
    """Expose ``WebBenchmarkAdapter`` through the runtime episode interface."""

    def __init__(
        self,
        adapter: WebBenchmarkAdapter,
        task: BenchmarkTask,
        *,
        bindings: dict[str, str] | None = None,
        completions: set[str] | None = None,
        goal_id: str = "",
        goal_state: str = "",
    ) -> None:
        self._adapter = adapter
        self._task = task
        self._bindings = dict(bindings or {})
        self._completions = set(completions or set())
        self._goal_id = goal_id or task.task_id
        self._goal_state = goal_state
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)
        self._adapter.reset(self._task)

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation:
        self.requests.append(request)
        page = self._adapter.observe(self._task)
        return observation_from_live_sources(
            page=_annotated_page(
                page,
                bindings=self._bindings,
                completions=self._completions,
                goal_id=self._goal_id,
                goal_state=self._goal_state,
            ),
            page_state={"benchmark": {"solved": self._adapter.is_solved(self._task)}},
            response_to_request_id=request.request_id,
            captured_at_ms=int(time.time() * 1000),
        )

    def executors(self) -> dict[str, "_RuntimeWebExecutor"]:
        return {"dom": _RuntimeWebExecutor(self)}

    def _execute(self, skill_call: SkillCall) -> ExecutionResult:
        affordance_id = str(skill_call.params.get("affordance_id") or "")
        page = self._adapter.observe(self._task)
        affordance = page.by_id(affordance_id)
        if affordance is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"affordance not found: {affordance_id}",
            )
        return self._adapter.act(
            affordance,
            value=skill_call.params.get("value"),
            skill_id=skill_call.skill_id,
        )


class _RuntimeWebExecutor:
    def __init__(self, environment: RuntimeWebEnvironmentAdapter) -> None:
        self._environment = environment
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        return self._environment._execute(skill_call)


def _annotated_page(
    page: Any,
    *,
    bindings: dict[str, str],
    completions: set[str],
    goal_id: str,
    goal_state: str,
) -> Any:
    affordances = [
        _annotated_affordance(
            affordance,
            bindings=bindings,
            completions=completions,
            goal_id=goal_id,
            goal_state=goal_state,
        )
        for affordance in page.affordances
    ]
    return type(page)(
        page_id=page.page_id,
        url=page.url,
        affordances=affordances,
        captured_at_ms=page.captured_at_ms,
        raw_node_count=page.raw_node_count,
        kept_node_count=page.kept_node_count,
    )


def _annotated_affordance(
    affordance: Affordance,
    *,
    bindings: dict[str, str],
    completions: set[str],
    goal_id: str,
    goal_state: str,
) -> Affordance:
    locator = dict(affordance.locator)
    for parameter, target in bindings.items():
        if _matches_target(affordance, target):
            locator["binds_parameter"] = parameter
    if _matches_any_target(affordance, completions):
        locator["completion_for"] = goal_id
        locator["achieves"] = goal_state
    return Affordance(
        id=affordance.id,
        source=affordance.source,
        type=affordance.type,
        label=affordance.label,
        action=affordance.action,
        locator=locator,
        confidence=affordance.confidence,
        state=dict(affordance.state),
        safety_level=affordance.safety_level,
    )


def _matches_any_target(affordance: Affordance, targets: set[str]) -> bool:
    return any(_matches_target(affordance, target) for target in targets)


def _matches_target(affordance: Affordance, target: str) -> bool:
    return affordance.id == target or str(affordance.locator.get("selector", "")) == target
