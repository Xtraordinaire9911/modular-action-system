"""Tests for the shared runtime episode runner interface."""

from __future__ import annotations

import asyncio

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.episode import ObservationRequest
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec
from src.runtime.live_observation import bind_live_observation_to_request, observation_from_live_sources
from src.runtime.state_machine import RuntimeState


class _Executor:
    def __init__(self) -> None:
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=True,
            latency_ms=5,
            confidence=1.0,
        )


class _Adapter:
    def __init__(self) -> None:
        self.executor = _Executor()
        self.requests: list[ObservationRequest] = []
        self.reset_specs: list[RuntimeEpisodeSpec] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.reset_specs.append(spec)

    async def observe(self, request: ObservationRequest):
        self.requests.append(request)
        if request.reason == "initial_observation":
            observed = _live({"booking": {"confirmed": False}})
        else:
            observed = _live({"booking": {"confirmed": True}})
        return bind_live_observation_to_request(observed, request_id=request.request_id)

    def executors(self):
        return {"dom": self.executor}


def _live(page_state):
    page = PageAffordanceModel(
        page_id="booking",
        url="https://example.test/booking",
        affordances=[
            Affordance(
                "dom_confirm",
                "DOM",
                "button",
                "Confirm",
                "click",
                {
                    "entity_id": "booking",
                    "completion_for": "reserve",
                    "achieves": "booking.confirmed == true",
                },
                0.95,
            )
        ],
    )
    return observation_from_live_sources(page=page, page_state=page_state)


def test_runtime_episode_runner_executes_goal_through_cim_and_derives_metrics():
    adapter = _Adapter()
    runner = RuntimeEpisodeRunner()

    outcome = asyncio.run(
        runner.run_goal_episode(
            adapter,
            RuntimeEpisodeSpec(
                task_id="booking-task",
                goal_id="reserve",
                goal_state="booking.confirmed == true",
            ),
        )
    )

    assert outcome.result.state == RuntimeState.COMPLETED
    assert outcome.result.final_outcome_verified
    assert len(adapter.reset_specs) == 1
    assert [request.reason for request in adapter.requests] == ["initial_observation", "post_action_verification"]
    assert adapter.executor.calls[0].params["affordance_id"] == "dom_confirm"
    assert outcome.transition_ledger.records[0].episode_id == outcome.result.episode_id
    assert outcome.metrics.metadata["episode_ids"] == [outcome.result.episode_id]
    assert outcome.metrics.metadata["measurement_counts"]["primitive_actions"] == 1
