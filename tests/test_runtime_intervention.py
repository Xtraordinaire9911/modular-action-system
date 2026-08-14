"""Runtime integration tests for Project PiP supervision and lifecycle ordering."""

from __future__ import annotations

import asyncio
import copy

import pytest

from src.contracts.types import Affordance, Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.isolation.episode import BrowserWotIsolationProvider
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult
from src.runtime.episode import EpisodePolicy, ObservationRequest
from src.runtime.intervention import InMemoryInterventionBroker, InterventionAction, InterventionDecision
from src.runtime.live_observation import (
    LiveRuntimeObservation,
    bind_live_observation_to_request,
    observation_from_live_sources,
)
from src.runtime.state_machine import RuntimeState


class _Executor:
    def __init__(self, backend: str = "dom") -> None:
        self.backend = backend
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        return ExecutionResult(skill_call.skill_id, self.backend, True, 1.0, 1.0)


class _ObservationProvider:
    def __init__(self, observations: list[LiveRuntimeObservation | Observation]) -> None:
        self.observations = list(observations)
        self.requests: list[ObservationRequest] = []
        self.begun_episode_ids: list[str] = []

    def begin_episode(self, episode_id: str) -> None:
        self.begun_episode_ids.append(episode_id)

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation | Observation:
        self.requests.append(request)
        observed = self.observations.pop(0)
        if isinstance(observed, LiveRuntimeObservation):
            return bind_live_observation_to_request(observed, request_id=request.request_id)
        return observed


class _PostActionConflictManager(ContinuousInteractionManager):
    """Inject one fusion failure after the first executor call."""

    async def _run_fusion_gate(self, observation: Observation) -> RuntimeStepResult | None:
        _ = observation
        self._fusion_calls = getattr(self, "_fusion_calls", 0) + 1
        self._last_active_perception_trace = []
        self._last_fusion_decision = {}
        if self._fusion_calls == 2:
            self.state = RuntimeState.ESCALATED
            return RuntimeStepResult(
                self.state,
                None,
                recovery_tier=4,
                reason="post-action sources disagree",
                failure_boundary="recoverable_execution_failure",
                failure_type="sensory_conflict",
            )
        return None


def _high_risk_skill() -> SkillTuple:
    return SkillTuple(
        skill_id="unlock_door",
        description="Unlock the door",
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


def _verified_door_skill(*, safety_level: str = "high") -> SkillTuple:
    return SkillTuple(
        skill_id="unlock_verified",
        description="Unlock and verify the door",
        parameters_schema={},
        preconditions=[],
        postconditions=[Condition("page_state.door.unlocked == true")],
        allowed_backends=["dom"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=500,
        safety_level=safety_level,
        irreversible=False,
    )


def _booking_live(*, confirmed: bool, include_confirm: bool) -> LiveRuntimeObservation:
    affordances = []
    if include_confirm:
        affordances.append(
            Affordance(
                id="dom_confirm",
                source="DOM",
                type="button",
                label="Confirm",
                action="click",
                locator={
                    "entity_id": "booking",
                    "completion_for": "reserve",
                    "achieves": "booking.confirmed == true",
                },
                confidence=0.95,
            )
        )
    return observation_from_live_sources(
        page_state={"booking": {"confirmed": confirmed}},
        wot_affordances=affordances,
    )


def test_high_risk_action_waits_for_explicit_approval_before_executor_runs():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        manager = ContinuousInteractionManager(
            {"unlock_door": _high_risk_skill()},
            {"dom": executor},
            CognitiveMap(task_id="safe-door"),
            intervention_broker=broker,
        )

        run = asyncio.create_task(manager.run_skill(SkillCall("unlock_door", {}), Observation()))
        request = await broker.next_request(timeout_s=0.2)

        assert manager.state == RuntimeState.AWAITING_HUMAN
        assert executor.calls == []
        broker.resolve(request.intervention_id, InterventionDecision(InterventionAction.APPROVE, actor="fadi"))
        result = await run

        assert result.state == RuntimeState.COMPLETED
        assert len(executor.calls) == 1
        assert broker.ledger.records[0].decision == "approve"
        assert broker.ledger.records[0].actor == "fadi"

    asyncio.run(scenario())


def test_takeover_resume_reobserves_and_replans_instead_of_executing_stale_plan():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        provider = _ObservationProvider(
            [
                _booking_live(confirmed=False, include_confirm=True),
                _booking_live(confirmed=True, include_confirm=True),
            ]
        )
        manager = ContinuousInteractionManager(
            {},
            {"dom": executor},
            CognitiveMap(task_id="human-correction"),
            observation_provider=provider,
            intervention_broker=broker,
            episode_policy=EpisodePolicy(max_steps=3, deadline_s=2, require_fresh_observation=True),
        )

        initial = _booking_live(confirmed=False, include_confirm=False)
        run = asyncio.create_task(
            manager.run_observed_goal(
                initial,
                goal_id="reserve",
                goal_state="booking.confirmed == true",
            )
        )
        request = await broker.next_request(timeout_s=0.2)

        assert manager.state == RuntimeState.AWAITING_HUMAN
        assert executor.calls == []
        broker.resolve(
            request.intervention_id,
            InterventionDecision(
                InterventionAction.RESUME,
                actor="fadi",
                note="made the missing control available",
                correction_applied=True,
            ),
        )
        result = await run

        assert result.state == RuntimeState.COMPLETED
        assert result.final_outcome_verified
        assert [call.params["affordance_id"] for call in executor.calls] == ["dom_confirm"]
        assert [request.reason for request in provider.requests] == [
            "human_intervention_resume",
            "post_action_verification",
        ]
        record = broker.ledger.records[0]
        assert record.reobserved and record.replanned and record.correction_applied

    asyncio.run(scenario())


def test_takeover_resume_does_not_repeat_a_durable_action_the_human_completed():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        provider = _ObservationProvider([observation_from_live_sources(page_state={"door": {"unlocked": True}})])
        manager = ContinuousInteractionManager(
            {"unlock_verified": _verified_door_skill()},
            {"dom": executor},
            CognitiveMap(task_id="human-unlocked-door"),
            observation_provider=provider,
            intervention_broker=broker,
        )

        run = asyncio.create_task(manager.run_skill(SkillCall("unlock_verified", {}), Observation()))
        request = await broker.next_request(timeout_s=0.2)
        broker.resolve(
            request.intervention_id,
            InterventionDecision(
                InterventionAction.RESUME,
                actor="fadi",
                correction_applied=True,
            ),
        )
        result = await run

        assert result.state == RuntimeState.COMPLETED
        assert result.final_outcome_verified
        assert "human correction" in result.reason
        assert executor.calls == []
        assert broker.pending_requests() == []
        record = broker.ledger.records[0]
        assert record.reobserved and record.replanned

    asyncio.run(scenario())


def test_operator_cancel_is_scoped_to_one_episode_and_does_not_poison_the_next_run():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        provider = _ObservationProvider([observation_from_live_sources(page_state={"door": {"unlocked": True}})])
        manager = ContinuousInteractionManager(
            {
                "unlock_door": _high_risk_skill(),
                "unlock_verified": _verified_door_skill(safety_level="low"),
            },
            {"dom": executor},
            CognitiveMap(task_id="reusable-manager"),
            observation_provider=provider,
            intervention_broker=broker,
        )

        cancelled_run = asyncio.create_task(manager.run_skill(SkillCall("unlock_door", {}), Observation()))
        request = await broker.next_request(timeout_s=0.2)
        broker.resolve(
            request.intervention_id,
            InterventionDecision(InterventionAction.CANCEL, actor="fadi", note="stop this task"),
        )
        cancelled = await cancelled_run
        assert cancelled.state == RuntimeState.ESCALATED
        assert executor.calls == []

        next_run = await manager.run_skill(
            SkillCall("unlock_verified", {}),
            observation_from_live_sources(page_state={"door": {"unlocked": False}}).observation,
        )
        assert next_run.state == RuntimeState.COMPLETED
        assert len(executor.calls) == 1

    asyncio.run(scenario())


def test_post_action_skill_conflict_pauses_and_resume_verifies_before_any_repeat():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        provider = _ObservationProvider(
            [
                observation_from_live_sources(page_state={"door": {"unlocked": False}}),
                observation_from_live_sources(page_state={"door": {"unlocked": True}}),
            ]
        )
        manager = _PostActionConflictManager(
            {"unlock_verified": _verified_door_skill(safety_level="low")},
            {"dom": executor},
            CognitiveMap(task_id="post-action-skill-conflict"),
            observation_provider=provider,
            intervention_broker=broker,
            episode_policy=EpisodePolicy(require_fresh_observation=True),
        )

        run = asyncio.create_task(manager.run_skill(SkillCall("unlock_verified", {}), Observation()))
        request = await broker.next_request(timeout_s=0.2)
        assert manager.state == RuntimeState.AWAITING_HUMAN
        assert len(executor.calls) == 1
        broker.resolve(
            request.intervention_id,
            InterventionDecision(InterventionAction.RESUME, actor="fadi", correction_applied=True),
        )
        result = await run

        assert result.state == RuntimeState.COMPLETED
        assert result.final_outcome_verified
        assert len(executor.calls) == 1
        assert [request.reason for request in provider.requests] == [
            "post_action_verification",
            "human_intervention_resume",
        ]
        assert broker.ledger.records[0].reobserved and broker.ledger.records[0].replanned

    asyncio.run(scenario())


def test_post_action_primitive_conflict_pauses_and_resume_replans_the_goal():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker()
        executor = _Executor()
        provider = _ObservationProvider(
            [
                _booking_live(confirmed=False, include_confirm=True),
                _booking_live(confirmed=True, include_confirm=True),
            ]
        )
        manager = _PostActionConflictManager(
            {},
            {"dom": executor},
            CognitiveMap(task_id="post-action-goal-conflict"),
            observation_provider=provider,
            intervention_broker=broker,
            episode_policy=EpisodePolicy(max_steps=3, deadline_s=2, require_fresh_observation=True),
        )

        run = asyncio.create_task(
            manager.run_observed_goal(
                _booking_live(confirmed=False, include_confirm=True),
                goal_id="reserve",
                goal_state="page_state.booking.confirmed == true",
            )
        )
        request = await broker.next_request(timeout_s=0.2)
        assert manager.state == RuntimeState.AWAITING_HUMAN
        assert len(executor.calls) == 1
        broker.resolve(
            request.intervention_id,
            InterventionDecision(InterventionAction.RESUME, actor="fadi", correction_applied=True),
        )
        result = await run

        assert result.state == RuntimeState.COMPLETED
        assert result.final_outcome_verified
        assert len(executor.calls) == 1
        assert broker.ledger.records[0].reobserved and broker.ledger.records[0].replanned

    asyncio.run(scenario())


class _BrowserSurface:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def recreate(self) -> None:
        self.events.append("browser:recreate")

    async def stop(self) -> None:
        self.events.append("browser:stop")


class _ControlSurface:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.state = {"state": {"room": {"temperature": 19}}, "faults": {"room": {"type": "timeout"}}}
        self._checkpoint: dict | None = None
        self.lease_id = ""

    async def acquire_lease(self, episode_id: str) -> dict:
        self.events.append("wot:acquire")
        self._checkpoint = copy.deepcopy(self.state)
        self.lease_id = f"lease:{episode_id}"
        self.state = {"state": {"room": {"temperature": 20}}, "faults": {}}
        return {
            "status": "acquired",
            "episode_id": episode_id,
            "lease_id": self.lease_id,
            "checkpoint": copy.deepcopy(self._checkpoint),
        }

    async def restore_lease(self) -> dict:
        self.events.append("wot:restore")
        assert self._checkpoint is not None
        self.state = copy.deepcopy(self._checkpoint)
        return copy.deepcopy(self.state)

    async def release_lease(self) -> dict:
        self.events.append("wot:release")
        assert self._checkpoint is not None
        self.state = copy.deepcopy(self._checkpoint)
        self._checkpoint = None
        self.lease_id = ""
        return copy.deepcopy(self.state)


def test_isolated_entrypoint_provisions_before_observation_and_restores_after_result():
    async def scenario() -> None:
        events: list[str] = []
        control = _ControlSurface(events)
        baseline = copy.deepcopy(control.state)
        isolation = BrowserWotIsolationProvider(_BrowserSurface(events), control)

        class Provider(_ObservationProvider):
            async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation | Observation:
                events.append(f"observe:{request.reason}")
                return await super().observe(request)

        provider = Provider([observation_from_live_sources(page_state={"task": {"done": True}})])
        cognitive_map = CognitiveMap(task_id="isolated-goal")
        cognitive_map.page_state["stale"] = {"value": "must disappear"}
        manager = ContinuousInteractionManager(
            {},
            {},
            cognitive_map,
            observation_provider=provider,
            isolation_provider=isolation,
        )

        result = await manager.run_isolated_goal(goal_id="already_done", goal_state="task.done == true")

        assert result.state == RuntimeState.COMPLETED
        assert result.episode_id == provider.requests[0].episode_id == provider.begun_episode_ids[0]
        assert "stale" not in cognitive_map.page_state
        assert control.state == baseline
        assert events == [
            "wot:acquire",
            "browser:recreate",
            "observe:episode_start",
            "wot:restore",
            "browser:stop",
            "wot:release",
        ]
        assert isolation.active_session is None

    asyncio.run(scenario())


def test_isolated_entrypoint_disposes_when_episode_initialization_fails():
    async def scenario() -> None:
        events: list[str] = []
        control = _ControlSurface(events)
        baseline = copy.deepcopy(control.state)
        isolation = BrowserWotIsolationProvider(_BrowserSurface(events), control)

        class BrokenProvider(_ObservationProvider):
            def begin_episode(self, episode_id: str) -> None:
                super().begin_episode(episode_id)
                raise RuntimeError("cannot initialize observation episode")

        provider = BrokenProvider([])
        manager = ContinuousInteractionManager(
            {},
            {},
            CognitiveMap(task_id="broken-isolated-goal"),
            observation_provider=provider,
            isolation_provider=isolation,
        )

        with pytest.raises(RuntimeError, match="cannot initialize"):
            await manager.run_isolated_goal(goal_id="never-runs", goal_state="task.done == true")

        assert control.state == baseline
        assert isolation.active_session is None
        assert manager._active_isolation_session is None
        assert events == [
            "wot:acquire",
            "browser:recreate",
            "wot:restore",
            "browser:stop",
            "wot:release",
        ]

    asyncio.run(scenario())
