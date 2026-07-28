"""Observe-plan-act-reobserve tests for bounded no-skill goals."""

import asyncio

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.episode import EpisodePolicy, ObservationRequest
from src.runtime.live_observation import LiveRuntimeObservation, observation_from_live_sources
from src.runtime.state_machine import RuntimeState


def _affordances(include_time=True, include_visual=False):
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
    return values


def _live(state, *, include_time=True, include_visual=False):
    page = PageAffordanceModel(
        page_id="booking",
        url="https://example.test/booking",
        affordances=_affordances(include_time=include_time, include_visual=include_visual),
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
        return self.observations.pop(0)


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
