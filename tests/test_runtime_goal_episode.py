"""Observe-plan-act-reobserve tests for bounded no-skill goals."""

import asyncio

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.affordance_controller import AffordanceController
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.episode import CancellationToken, EpisodePolicy, ObservationRequest
from src.runtime.live_observation import (
    LiveRuntimeObservation,
    bind_live_observation_to_request,
    observation_from_live_sources,
)
from src.runtime.state_machine import RuntimeOutcome, RuntimeState
from src.runtime.system2_planner import System2Planner


def _affordances(include_time=True, include_visual=False, include_visual_room=False):
    values = [
        Affordance(
            "dom_room",
            "DOM",
            "input",
            "Room",
            "type",
            {"entity_id": "form", "parameter": "room"},
            0.95,
        ),
        Affordance(
            "dom_confirm",
            "DOM",
            "button",
            "Confirm",
            "click",
            {"entity_id": "booking", "completion_for": "reserve", "achieves": "booking.confirmed == true"},
            0.95,
        ),
    ]
    if include_time:
        values.insert(
            1,
            Affordance(
                "dom_time",
                "DOM",
                "input",
                "Time",
                "type",
                {"entity_id": "form", "parameter": "time"},
                0.95,
            ),
        )
    if include_visual:
        values.append(
            Affordance(
                "visual_confirm",
                "VISUAL",
                "button",
                "Confirm",
                "click",
                {
                    "entity_id": "booking",
                    "completion_for": "reserve",
                    "achieves": "booking.confirmed == true",
                    "mark_id": "M1",
                },
                0.9,
            )
        )
    if include_visual_room:
        values.append(
            Affordance(
                "visual_room",
                "VISUAL",
                "input",
                "Room",
                "type",
                {"entity_id": "form", "parameter": "room", "mark_id": "M2"},
                0.88,
            )
        )
    return values


def _live(state, *, include_time=True, include_visual=False, include_visual_room=False):
    page = PageAffordanceModel(
        page_id="booking",
        url="https://example.test/booking",
        affordances=_affordances(
            include_time=include_time,
            include_visual=include_visual,
            include_visual_room=include_visual_room,
        ),
    )
    return observation_from_live_sources(page=page, page_state=state)


class _Executor:
    def __init__(self, backend, outcomes=None):
        self.backend = backend
        self.outcomes = list(outcomes or [])
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call, observation):
        self.calls.append(skill_call)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return ExecutionResult(skill_call.skill_id, self.backend, True, 1, 1.0)


class _Provider:
    def __init__(self, observations):
        self.observations = list(observations)
        self.requests: list[ObservationRequest] = []

    async def observe(self, request):
        self.requests.append(request)
        observed = self.observations.pop(0)
        if isinstance(observed, LiveRuntimeObservation):
            return bind_live_observation_to_request(observed, request_id=request.request_id)
        return observed


def test_goal_reobserves_and_replans_between_every_primitive_action():
    provider = _Provider(
        [
            _live({"form": {"room": "A"}, "booking": {"confirmed": False}}),
            _live({"form": {"room": "A", "time": "14:00"}, "booking": {"confirmed": False}}),
            _live({"booking": {"confirmed": True}}),
        ]
    )
    executor = _Executor("dom")
    initial = _live({"booking": {"confirmed": False}})
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="goal-loop"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(max_steps=6, deadline_s=2, require_fresh_observation=True),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            initial,
            goal_id="reserve",
            goal_state="booking.confirmed == true",
            parameters={"room": "A", "time": "14:00"},
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.final_outcome_verified
    assert [call.params["affordance_id"] for call in executor.calls] == ["dom_room", "dom_time", "dom_confirm"]
    assert len(provider.requests) == 3
    assert result.attempts == 3
    assert len(result.transition_ids) == 3
    assert result.final_verification_transition_id == result.transition_ids[-1]


def test_primitive_expected_effect_requires_fresh_verified_state():
    provider = _Provider([_live({"form": {"room": "B"}, "booking": {"confirmed": False}})])
    executor = _Executor("dom")
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="goal-expected-effect"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(max_steps=3, max_retry_attempts=0, deadline_s=2, require_fresh_observation=True),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live({"booking": {"confirmed": False}}, include_time=False),
            goal_id="reserve",
            goal_state="booking.confirmed == true",
            parameters={"room": "A"},
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.failure_type == "postcondition_failed"
    assert result.outcome is RuntimeOutcome.USER_ACTION_REQUIRED
    assert "expected_effect=\"form.room == 'A'\"" in manager.transition_ledger.records[0].failure_reason
    assert manager.transition_ledger.records[0].execution_success is True
    assert manager.transition_ledger.records[0].postcondition_passed is False


def test_disappeared_affordance_causes_replan_and_escalation_not_stale_execution():
    provider = _Provider([_live({"form": {"room": "A"}, "booking": {"confirmed": False}}, include_time=False)])
    executor = _Executor("dom")
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="goal-disappearance"),
        observation_provider=provider,
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live({"booking": {"confirmed": False}}),
            goal_id="reserve",
            goal_state="booking.confirmed == true",
            parameters={"room": "A", "time": "14:00"},
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.failure_type == "insufficient_affordance_plan"
    assert [call.params["affordance_id"] for call in executor.calls] == ["dom_room"]
    assert "dom_time" not in manager.cognitive_map.runtime_affordances


def test_goal_executes_visual_alternative_after_dom_failure():
    failed = ExecutionResult("reserve", "dom", False, 1, 0.0, failure_reason="selector_not_found")
    dom = _Executor("dom", [failed])
    visual = _Executor("visual")
    provider = _Provider(
        [
            _live({"booking": {"confirmed": False}}, include_visual=True),
            _live({"booking": {"confirmed": True}}, include_visual=True),
        ]
    )
    initial = LiveRuntimeObservation(
        observation=Observation(accessibility_tree={"page_state": {"booking": {"confirmed": False}}}),
        affordances=[
            affordance
            for affordance in _affordances(include_time=False, include_visual=True)
            if "confirm" in affordance.id
        ],
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": dom, "visual": visual},
        CognitiveMap(task_id="goal-reroute"),
        observation_provider=provider,
    )

    result = asyncio.run(
        manager.run_observed_goal(
            initial,
            goal_id="reserve",
            goal_state="booking.confirmed == true",
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.recovery_attempted and result.recovery_succeeded
    assert len(dom.calls) == 1
    assert len(visual.calls) == 1
    assert visual.calls[0].params["affordance_id"] == "visual_confirm"
    records = manager.transition_ledger.records
    assert [record.backend for record in records] == ["dom", "visual"]
    assert records[0].recovery_of_transition_id == ""
    assert records[1].recovery_action == "reroute"
    assert records[1].recovery_of_transition_id == records[0].transition_id


def test_partial_state_delta_cannot_authorize_reroute_to_retained_affordance():
    failed = ExecutionResult("reserve", "dom", False, 1, 0.0, failure_reason="selector_not_found")
    dom = _Executor("dom", [failed])
    visual = _Executor("visual")
    provider = _Provider([Observation(accessibility_tree={"page_state": {"booking": {"confirmed": False}}})])
    initial = LiveRuntimeObservation(
        observation=Observation(accessibility_tree={"page_state": {"booking": {"confirmed": False}}}),
        affordances=[
            affordance
            for affordance in _affordances(include_time=False, include_visual=True)
            if "confirm" in affordance.id
        ],
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": dom, "visual": visual},
        CognitiveMap(task_id="partial-reroute"),
        observation_provider=provider,
    )

    result = asyncio.run(manager.run_observed_goal(initial, goal_id="reserve", goal_state="booking.confirmed == true"))

    assert result.state == RuntimeState.ESCALATED
    assert result.outcome is RuntimeOutcome.TERMINAL_FAILURE
    assert len(dom.calls) == 1
    assert visual.calls == []
    assert "complete fresh affordance snapshot" in result.reason


class _CancellingPlanner:
    def __init__(self, token):
        self.token = token
        self.delegate = System2Planner(AffordanceController())

    def plan(self, context, **kwargs):
        plan = self.delegate.plan(context, **kwargs)
        self.token.cancel("operator cancelled during planning")
        return plan


def test_cancellation_at_attempt_boundary_is_not_projected_as_budget_exhaustion():
    token = CancellationToken()
    executor = _Executor("dom")
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="cancel-at-attempt"),
        system2_planner=_CancellingPlanner(token),
        cancellation_token=token,
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live({"booking": {"confirmed": False}}, include_time=False),
            goal_id="reserve",
            goal_state="booking.confirmed == true",
        )
    )

    assert result.failure_type == "cancelled"
    assert result.outcome is RuntimeOutcome.CANCELLED
    assert result.reason == "operator cancelled during planning"
    assert executor.calls == []


class _CancellingExecutor(_Executor):
    def __init__(self, token):
        super().__init__("dom")
        self.token = token

    async def execute(self, skill_call, observation):
        result = await super().execute(skill_call, observation)
        self.token.cancel("operator cancelled after action")
        return result


def test_post_action_cancellation_wins_over_newly_satisfied_goal():
    token = CancellationToken()
    executor = _CancellingExecutor(token)
    provider = _Provider([_live({"booking": {"confirmed": True}}, include_time=False)])
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="cancel-after-action"),
        observation_provider=provider,
        cancellation_token=token,
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live({"booking": {"confirmed": False}}, include_time=False),
            goal_id="reserve",
            goal_state="booking.confirmed == true",
        )
    )

    assert result.failure_type == "cancelled"
    assert result.outcome is RuntimeOutcome.CANCELLED
    assert not result.final_outcome_verified


def test_goal_reroute_rejects_non_equivalent_visual_alternative():
    failed = ExecutionResult("reserve", "dom", False, 1, 0.0, failure_reason="selector_not_found")
    dom = _Executor("dom", [failed])
    visual = _Executor("visual")
    non_equivalent_visual = Affordance(
        "visual_cancel",
        "VISUAL",
        "button",
        "Cancel",
        "click",
        {
            "entity_id": "booking",
            "completion_for": "reserve",
            "achieves": "booking.cancelled == true",
            "mark_id": "M9",
        },
        0.9,
    )
    provider = _Provider([_live({"booking": {"confirmed": False}}, include_time=False)])
    initial = LiveRuntimeObservation(
        observation=Observation(accessibility_tree={"page_state": {"booking": {"confirmed": False}}}),
        affordances=[
            *[affordance for affordance in _affordances(include_time=False) if affordance.id == "dom_confirm"],
            non_equivalent_visual,
        ],
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": dom, "visual": visual},
        CognitiveMap(task_id="goal-reroute-equivalence"),
        observation_provider=provider,
    )

    result = asyncio.run(
        manager.run_observed_goal(
            initial,
            goal_id="reserve",
            goal_state="booking.confirmed == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.replan_count == 1
    assert result.outcome is RuntimeOutcome.USER_ACTION_REQUIRED
    assert len(dom.calls) == 1
    assert visual.calls == []
    assert [record.backend for record in manager.transition_ledger.records] == ["dom"]


def test_failed_primitive_retry_keeps_retry_label_before_next_reroute_transition():
    failed = ExecutionResult("reserve", "dom", False, 1, 0.0, failure_reason="timeout")
    dom = _Executor("dom", [failed, failed])
    visual = _Executor("visual")
    provider = _Provider(
        [
            _live({"form": {}}, include_time=False, include_visual_room=True),
            _live({"form": {}}, include_time=False, include_visual_room=True),
            _live({"form": {"room": "A"}}, include_time=False, include_visual_room=True),
        ]
    )
    initial = LiveRuntimeObservation(
        observation=Observation(accessibility_tree={"page_state": {"form": {}}}),
        affordances=[
            affordance
            for affordance in _affordances(include_time=False, include_visual_room=True)
            if affordance.locator.get("parameter") == "room"
        ],
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": dom, "visual": visual},
        CognitiveMap(task_id="goal-retry-then-reroute"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(
            max_steps=5,
            deadline_s=2,
            max_retry_attempts=1,
            max_attempts_per_backend=2,
            require_fresh_observation=True,
        ),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            initial,
            goal_id="reserve",
            goal_state="form.room == 'A'",
            parameters={"room": "A"},
        )
    )

    assert result.state == RuntimeState.COMPLETED
    records = manager.transition_ledger.records
    assert [record.backend for record in records] == ["dom", "dom", "visual"]
    assert [record.recovery_action for record in records] == ["retry", "retry", "reroute"]
    assert records[0].recovery_of_transition_id == ""
    assert records[1].recovery_of_transition_id == records[0].transition_id
    assert records[2].recovery_of_transition_id == records[1].transition_id
