"""Tests for the shared runtime episode runner interface."""

from __future__ import annotations

import asyncio

import pytest

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall, SkillTuple
from src.isolation import (
    AgentInputGuardedExecutor,
    EpisodeIsolationSession,
    InputLease,
    InputLeaseDenied,
    InputOwner,
    IsolationState,
)
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.episode import EpisodeContext, ObservationRequest
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec, _guard_agent_executors
from src.runtime.intervention import (
    InMemoryInterventionBroker,
    InterventionAction,
    InterventionDecision,
    InterventionLedger,
)
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


class _IsolationProvider:
    """Small lifecycle recorder used to prove runner/CIM ordering."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.session: EpisodeIsolationSession | None = None

    async def provision(self, episode: EpisodeContext) -> EpisodeIsolationSession:
        self.events.append("isolation:provision")
        self.session = EpisodeIsolationSession(
            task_id=episode.task_id,
            episode_id=episode.episode_id,
            checkpoint={},
            state=IsolationState.ACTIVE,
        )
        return self.session

    async def checkpoint(self, session: EpisodeIsolationSession) -> dict[str, object]:
        self._require_session(session)
        return dict(session.checkpoint)

    async def pause(self, session: EpisodeIsolationSession) -> None:
        self._require_session(session)
        self.events.append("isolation:pause")
        session.state = IsolationState.PAUSED

    async def resume(self, session: EpisodeIsolationSession) -> None:
        self._require_session(session)
        self.events.append("isolation:resume")
        session.state = IsolationState.ACTIVE

    async def restore(self, session: EpisodeIsolationSession) -> None:
        self._require_session(session)
        self.events.append("isolation:restore")
        session.restored = True
        session.state = IsolationState.RESTORED

    async def dispose(self, session: EpisodeIsolationSession) -> None:
        self._require_session(session)
        self.events.append("isolation:dispose")
        session.restored = True
        session.disposed = True
        session.state = IsolationState.DISPOSED

    def _require_session(self, session: EpisodeIsolationSession) -> None:
        assert session is self.session


class _IsolatedAdapter(_Adapter):
    def __init__(self, events: list[str], *, fail_on_observe: bool = False) -> None:
        super().__init__()
        self.events = events
        self.fail_on_observe = fail_on_observe
        self.begun_episode_ids: list[str] = []

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        self.events.append("adapter:reset")
        await super().reset(spec)

    def begin_episode(self, episode_id: str) -> None:
        self.events.append("adapter:begin_episode")
        self.begun_episode_ids.append(episode_id)

    async def observe(self, request: ObservationRequest):
        self.events.append(f"adapter:observe:{request.reason}")
        self.requests.append(request)
        if self.fail_on_observe:
            raise RuntimeError("initial observation failed")
        observed = _live({"booking": {"confirmed": len(self.requests) > 1}})
        return bind_live_observation_to_request(observed, request_id=request.request_id)


def _high_risk_skill() -> SkillTuple:
    return SkillTuple(
        skill_id="confirm_booking",
        description="Confirm a booking",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["dom"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=500,
        safety_level="high",
        irreversible=False,
    )


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


def test_runner_level_isolation_provisions_before_first_observation_and_skips_adapter_reset():
    events: list[str] = []
    adapter = _IsolatedAdapter(events)
    isolation = _IsolationProvider(events)
    runner = RuntimeEpisodeRunner(isolation_provider=isolation)

    outcome = asyncio.run(
        runner.run_goal_episode(
            adapter,
            RuntimeEpisodeSpec(
                task_id="isolated-booking",
                goal_id="reserve",
                goal_state="booking.confirmed == true",
            ),
        )
    )

    assert outcome.result.state == RuntimeState.COMPLETED
    assert adapter.reset_specs == []
    assert adapter.begun_episode_ids == [outcome.result.episode_id]
    assert adapter.requests[0].episode_id == outcome.result.episode_id
    assert [request.reason for request in adapter.requests] == ["episode_start", "post_action_verification"]
    assert events == [
        "isolation:provision",
        "adapter:begin_episode",
        "adapter:observe:episode_start",
        "adapter:observe:post_action_verification",
        "isolation:dispose",
    ]
    assert isolation.session is not None and isolation.session.disposed


def test_per_run_dependencies_support_supervision_and_expose_the_intervention_ledger():
    async def scenario() -> None:
        events: list[str] = []
        adapter = _IsolatedAdapter(events)
        isolation = _IsolationProvider(events)
        broker = InMemoryInterventionBroker()
        ledger = InterventionLedger()
        runner = RuntimeEpisodeRunner(skill_library={"confirm_booking": _high_risk_skill()})

        pending_run = asyncio.create_task(
            runner.run_skill_episode(
                adapter,
                SkillCall("confirm_booking", {}),
                RuntimeEpisodeSpec(task_id="supervised-booking"),
                isolation_provider=isolation,
                intervention_broker=broker,
                intervention_ledger=ledger,
            )
        )
        request = await broker.next_request(timeout_s=0.2)

        assert events[-1] == "isolation:pause"
        assert adapter.executor.calls == []
        broker.resolve(
            request.intervention_id,
            InterventionDecision(InterventionAction.APPROVE, actor="operator"),
        )
        outcome = await pending_run

        assert outcome.result.state == RuntimeState.COMPLETED
        assert outcome.intervention_ledger is ledger
        assert len(outcome.intervention_ledger.records) == 1
        assert outcome.intervention_ledger.records[0].decision == "approve"
        assert len(adapter.executor.calls) == 1
        assert events == [
            "isolation:provision",
            "adapter:begin_episode",
            "adapter:observe:episode_start",
            "isolation:pause",
            "isolation:resume",
            "adapter:observe:post_action_verification",
            "isolation:dispose",
        ]

    asyncio.run(scenario())


def test_isolated_runner_disposes_when_the_first_observation_fails():
    events: list[str] = []
    adapter = _IsolatedAdapter(events, fail_on_observe=True)
    isolation = _IsolationProvider(events)
    runner = RuntimeEpisodeRunner(isolation_provider=isolation)

    with pytest.raises(RuntimeError, match="initial observation failed"):
        asyncio.run(
            runner.run_goal_episode(
                adapter,
                RuntimeEpisodeSpec(task_id="broken-isolated-booking", goal_id="reserve"),
            )
        )

    assert adapter.reset_specs == []
    assert events == [
        "isolation:provision",
        "adapter:begin_episode",
        "adapter:observe:episode_start",
        "isolation:dispose",
    ]
    assert isolation.session is not None and isolation.session.disposed


def test_runner_guard_wraps_lease_aware_executors_exactly_once() -> None:
    class Guard:
        def __init__(self) -> None:
            self.lease = InputLease(InputOwner.AGENT)

        def require_input(self, actor: InputOwner) -> None:
            self.lease.require(actor)

        def input_action(self, actor: InputOwner):
            return self.lease.input_action(actor)

    async def scenario() -> None:
        raw = _Executor()
        guard = Guard()
        once = _guard_agent_executors({"dom": raw}, guard)  # type: ignore[arg-type]
        assert isinstance(once["dom"], AgentInputGuardedExecutor)
        twice = _guard_agent_executors(once, guard)  # type: ignore[arg-type]
        assert twice["dom"] is once["dom"]

        guard.lease.transfer_to(InputOwner.HUMAN)
        with pytest.raises(InputLeaseDenied):
            await twice["dom"].execute(SkillCall("blocked", {}), Observation())
        assert raw.calls == []

        guard.lease.transfer_to(InputOwner.AGENT)
        result = await twice["dom"].execute(SkillCall("allowed", {}), Observation())
        assert result.success
        assert [call.skill_id for call in raw.calls] == ["allowed"]

    asyncio.run(scenario())
