from __future__ import annotations

import asyncio

from src.contracts.types import Affordance, Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.episode import ObservationRequest
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_observation import bind_live_observation_to_request, observation_from_live_sources
from src.runtime.state_machine import RuntimeState


class _Executor:
    def __init__(self) -> None:
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        return ExecutionResult(skill_call.skill_id, "dom", True, 1, 1.0)


class _Provider:
    def __init__(self, states: list[dict[str, object]] | None = None) -> None:
        self.requests: list[ObservationRequest] = []
        self.states = list(states or [{"booking": {"confirmed": True}}])

    async def observe(self, request: ObservationRequest):
        self.requests.append(request)
        observed = _live(self.states.pop(0))
        return bind_live_observation_to_request(observed, request_id=request.request_id)


def _skill(skill_id: str = "confirm_booking") -> SkillTuple:
    return SkillTuple(
        skill_id=skill_id,
        description="Confirm a room booking",
        parameters_schema={"room": "str"},
        preconditions=[],
        postconditions=[Condition("booking.confirmed == true")],
        allowed_backends=["dom"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=5_000,
        safety_level="medium",
        irreversible=False,
    )


def _live(page_state: dict[str, object]):
    page = PageAffordanceModel(
        page_id="booking",
        url="https://example.test/booking",
        affordances=[
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
                "Confirm booking",
                "click",
                {
                    "entity_id": "booking",
                    "completion_for": "confirm_booking",
                    "achieves": "booking.confirmed == true",
                },
                0.95,
            ),
        ],
    )
    return observation_from_live_sources(page=page, page_state=page_state)


def _goal(goal_id: str = "confirm_booking", *, room: object = "A") -> GoalSpec:
    return GoalSpec(
        goal_id=goal_id,
        goal_state="booking.confirmed == true",
        parameters={"room": room},
        source="demo",
    )


def test_matching_durable_skill_is_bound_and_traced_before_primitive_execution() -> None:
    skill = _skill()
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"dom": executor},
        CognitiveMap(task_id="skill-bound-goal"),
        observation_provider=_Provider(
            [
                {"form": {"room": "A"}, "booking": {"confirmed": False}},
                {"booking": {"confirmed": True}},
            ]
        ),
    )

    result = asyncio.run(manager.run_observed_goal(_live({"booking": {"confirmed": False}}), goal_spec=_goal()))

    assert result.state is RuntimeState.COMPLETED, (result.reason, result.failure_type, result.primitive_plan)
    assert result.final_outcome_verified
    assert result.goal_skill_selection == {
        "goal_id": "confirm_booking",
        "skill_id": "confirm_booking",
        "parameters": {"room": "A"},
        "preconditions": [],
        "postconditions": ["booking.confirmed == true"],
        "goal_states": [],
        "allowed_backends": ["dom"],
        "preferred_backends": ["dom"],
        "timeout_ms": 5000,
        "safety_level": "medium",
        "validation_status": "passed",
    }
    assert result.evidence_trace[0]["event"] == "goal_skill_selection"
    assert result.evidence_trace[0]["transition_ids"] == result.transition_ids
    assert executor.calls[0].skill_id == "confirm_booking"
    assert executor.calls[0].params["room"] == "A"


def test_matching_skill_rejects_invalid_parameters_before_execution() -> None:
    skill = _skill()
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"dom": executor},
        CognitiveMap(task_id="invalid-skill-bound-goal"),
    )

    result = asyncio.run(manager.run_goal(goal_spec=_goal(room=7), observation=Observation()))

    assert result.state is RuntimeState.ESCALATED
    assert result.failure_type == "invalid_skill_parameters"
    assert result.user_action_required
    assert "must be str, got int" in result.reason
    assert result.goal_skill_selection["validation_status"] == "failed"
    assert result.evidence_trace[0]["transition_ids"] == []
    assert result.episode_id.startswith("episode-")
    assert executor.calls == []


def test_invalid_goal_spec_still_has_an_attributable_episode_id() -> None:
    manager = ContinuousInteractionManager({}, {}, CognitiveMap(task_id="invalid-goal-spec"))

    result = asyncio.run(
        manager.run_goal(
            goal_spec=GoalSpec(goal_id="", goal_state="", parameters={}, source="demo"),
            observation=Observation(),
        )
    )

    assert result.state is RuntimeState.ESCALATED
    assert result.failure_type == "invalid_goal_spec"
    assert result.episode_id.startswith("episode-")


def test_ambiguous_semantic_skill_mapping_still_has_an_attributable_episode_id() -> None:
    first = _skill("first_booking")
    second = _skill("second_booking")
    first.goal_states = ["room_booked"]
    second.goal_states = ["room_booked"]
    manager = ContinuousInteractionManager(
        {first.skill_id: first, second.skill_id: second},
        {},
        CognitiveMap(task_id="ambiguous-goal-skill"),
    )

    result = asyncio.run(
        manager.run_goal(
            goal_spec=GoalSpec(
                goal_id="room_booked",
                goal_state="room_booked",
                parameters={"room": "A"},
                source="demo",
            ),
            observation=Observation(),
        )
    )

    assert result.state is RuntimeState.ESCALATED
    assert result.failure_type == "ambiguous_skill_selection"
    assert result.episode_id.startswith("episode-")


def test_goal_without_matching_skill_keeps_zero_shot_behavior_and_no_selection_claim() -> None:
    durable_skill = _skill()
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {durable_skill.skill_id: durable_skill},
        {"dom": executor},
        CognitiveMap(task_id="zero-shot-goal"),
        observation_provider=_Provider(),
    )
    zero_shot_goal = GoalSpec(
        goal_id="one_off_booking",
        goal_state="booking.confirmed == true",
        parameters={},
        source="demo",
    )

    result = asyncio.run(manager.run_observed_goal(_live({"booking": {"confirmed": False}}), goal_spec=zero_shot_goal))

    assert result.state is RuntimeState.COMPLETED
    assert result.final_outcome_verified
    assert result.goal_skill_selection == {}
    assert result.evidence_trace == []
    assert executor.calls[0].skill_id == "one_off_booking"


def test_semantic_intent_goal_uses_the_declared_reusable_skill() -> None:
    skill = _skill()
    skill.goal_states = ["room_booked"]
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"dom": executor},
        CognitiveMap(task_id="semantic-skill-bound-goal"),
        observation_provider=_Provider(
            [
                {"form": {"room": "A"}, "booking": {"confirmed": False}},
                {"booking": {"confirmed": True}},
            ]
        ),
    )
    goal = GoalSpec(
        goal_id="room_booked",
        goal_state="room_booked",
        parameters={"room": "A"},
        source="user_intent_parser",
    )

    result = asyncio.run(manager.run_observed_goal(_live({"booking": {"confirmed": False}}), goal_spec=goal))

    assert result.state is RuntimeState.COMPLETED, (result.reason, result.failure_type, result.primitive_plan)
    assert result.goal_skill_selection["skill_id"] == "confirm_booking"
    assert goal.goal_state == "room_booked"
    assert result.primitive_plan[-1]["expected_effect"] == "booking.confirmed == true"
    assert executor.calls[0].skill_id == "confirm_booking"


def test_selected_skill_precondition_is_enforced_before_any_action() -> None:
    skill = _skill()
    skill.goal_states = ["room_booked"]
    skill.preconditions = [Condition("booking.confirmed == true")]
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"dom": executor},
        CognitiveMap(task_id="skill-precondition"),
    )
    goal = GoalSpec(
        goal_id="room_booked",
        goal_state="booking.confirmed == true",
        parameters={"room": "A"},
    )

    result = asyncio.run(manager.run_observed_goal(_live({"booking": {"confirmed": False}}), goal_spec=goal))

    assert result.state is RuntimeState.FAILED
    assert result.failure_type == "precondition_failed"
    assert executor.calls == []


def test_selected_skill_rejects_a_backend_outside_its_contract() -> None:
    skill = _skill()
    skill.goal_states = ["room_booked"]
    skill.allowed_backends = ["visual"]
    skill.preferred_backends = ["visual"]
    executor = _Executor()
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"dom": executor},
        CognitiveMap(task_id="skill-backend-policy"),
    )
    goal = GoalSpec(
        goal_id="room_booked",
        goal_state="booking.confirmed == true",
        parameters={"room": "A"},
    )

    result = asyncio.run(manager.run_observed_goal(_live({"booking": {"confirmed": False}}), goal_spec=goal))

    assert result.state is RuntimeState.ESCALATED
    assert result.failure_type == "backend_not_allowed_by_skill"
    assert executor.calls == []
