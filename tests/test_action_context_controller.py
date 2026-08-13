from src.runtime.action_context import build_action_context
from src.runtime.affordance_controller import AffordanceController
from src.runtime.cognitive_map import CognitiveMap, Conflict, RuntimeAffordance, StateAssertion
from src.runtime.plan_validator import PlanValidator
from src.runtime.primitive_action import PrimitiveAction


def test_action_context_sanitizes_cognitive_map_for_planning():
    cmap = CognitiveMap(task_id="task_action_context")
    cmap.add_state_assertion(StateAssertion("booking", "service_available", True, "dom"))
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_room_input",
            source="dom",
            entity_id="booking_form",
            action_name="set_room",
            action_type="type",
            confidence=0.95,
            grounding={"selector": "#room", "label": "Room"},
        )
    )
    cmap.add_conflict(
        Conflict(
            id="booking.status",
            conflict_type="status_mismatch",
            sources=["dom", "wot"],
            description="Booking state disagrees.",
            severity="medium",
            conflict_mass=0.6,
        )
    )

    context = build_action_context(
        cmap,
        request_type="goal_spec",
        allowed_actions=["type", "click", "wait", "ask_user", "done"],
        safety_constraints=["no raw selector planning"],
    )

    assert context.task_id == "task_action_context"
    assert context.request_type == "goal_spec"
    assert context.state["dom"]["booking"]["service_available"] is True
    assert context.affordances[0].id == "dom_room_input"
    assert context.affordances[0].grounding == {"label": "Room"}
    assert context.unresolved_conflicts[0].id == "booking.status"
    assert context.allowed_actions == ["type", "click", "wait", "ask_user", "done"]


def test_affordance_controller_builds_typed_plan_without_durable_skill():
    cmap = CognitiveMap(task_id="task_no_skill_controller")
    for affordance in [
        RuntimeAffordance(
            id="dom_room_input",
            source="dom",
            entity_id="booking_form",
            action_name="room",
            action_type="type",
            confidence=0.95,
            grounding={"label": "Room", "selector": "#room", "parameter": "room"},
        ),
        RuntimeAffordance(
            id="dom_time_input",
            source="dom",
            entity_id="booking_form",
            action_name="time",
            action_type="type",
            confidence=0.94,
            grounding={"label": "Time", "selector": "#time", "parameter": "time"},
        ),
        RuntimeAffordance(
            id="dom_confirm_booking",
            source="dom",
            entity_id="booking_form",
            action_name="confirm_booking",
            action_type="click",
            confidence=0.93,
            grounding={
                "label": "Confirm booking",
                "selector": "#confirm",
                "achieves": "booking_status == 'confirmed'",
            },
        ),
    ]:
        cmap.add_affordance(affordance)
    context = build_action_context(
        cmap,
        request_type="goal_spec",
        allowed_actions=["type", "click", "wait", "ask_user", "done"],
    )

    plan = AffordanceController().plan(
        context,
        goal_state="booking_status == 'confirmed'",
        parameters={"room": "A", "time": "14:00"},
    )

    assert plan.actions == [
        PrimitiveAction("type", affordance_id="dom_room_input", value="A", expected_effect="room == 'A'"),
        PrimitiveAction("type", affordance_id="dom_time_input", value="14:00", expected_effect="time == '14:00'"),
        PrimitiveAction(
            "click",
            affordance_id="dom_confirm_booking",
            expected_effect="booking_status == 'confirmed'",
        ),
    ]
    assert plan.requires_escalation is False


def test_affordance_controller_escalates_when_context_is_ambiguous():
    cmap = CognitiveMap(task_id="task_no_skill_ambiguous")
    context = build_action_context(cmap, request_type="goal_spec")

    plan = AffordanceController().plan(
        context,
        goal_state="booking_status == 'confirmed'",
        parameters={"room": "A"},
    )

    assert plan.requires_escalation
    assert plan.actions == [
        PrimitiveAction("ask_user", expected_effect="provide affordance bindings for: room"),
    ]
    assert "no declared affordance binding for parameter 'room'" in plan.reason


def test_affordance_controller_uses_declarative_semantics_across_sources_without_label_keywords():
    cmap = CognitiveMap(task_id="task_cross_env_controller")
    for affordance in [
        RuntimeAffordance(
            id="visual_mark_7",
            source="visual",
            entity_id="form_surface",
            action_name="enter_value",
            action_type="input",
            confidence=0.91,
            grounding={"mark_id": "7", "parameter": "room_id"},
        ),
        RuntimeAffordance(
            id="wot_action_3",
            source="wot",
            entity_id="booking_service",
            action_name="invoke",
            action_type="action",
            confidence=0.88,
            grounding={"thing_id": "booking_service", "completion_for": "reserve_room_goal"},
        ),
    ]:
        cmap.add_affordance(affordance)
    context = build_action_context(cmap, request_type="goal_spec")

    plan = AffordanceController().plan(
        context,
        goal_id="reserve_room_goal",
        goal_state="device_states.booking.confirmed == true",
        parameters={"room_id": "A"},
    )

    assert plan.requires_escalation is False
    assert plan.actions == [
        PrimitiveAction("type", affordance_id="visual_mark_7", value="A", expected_effect="room_id == 'A'"),
        PrimitiveAction(
            "invoke",
            affordance_id="wot_action_3",
            expected_effect="device_states.booking.confirmed == true",
        ),
    ]


def test_plan_validator_rejects_unknown_affordance_and_disallowed_action():
    cmap = CognitiveMap(task_id="task_plan_validator")
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_confirm",
            source="dom",
            entity_id="booking_form",
            action_name="confirm",
            action_type="click",
            confidence=0.9,
            grounding={"label": "Confirm", "selector": "#confirm"},
        )
    )
    context = build_action_context(cmap, request_type="goal_spec", allowed_actions=["click", "ask_user", "done"])
    validator = PlanValidator()

    unknown = validator.validate(context, [PrimitiveAction("click", affordance_id="dom_missing")])
    disallowed = validator.validate(context, [PrimitiveAction("type", affordance_id="dom_confirm", value="x")])

    assert not unknown.valid
    assert unknown.errors == ["unknown affordance_id: dom_missing"]
    assert not disallowed.valid
    assert disallowed.errors == ["action type is not allowed: type"]


def test_plan_validator_rejects_primitive_incompatible_with_observed_affordance():
    cmap = CognitiveMap(task_id="task_plan_validator_primitive")
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_confirm",
            source="dom",
            entity_id="booking_form",
            action_name="confirm",
            action_type="button",
            confidence=0.9,
            grounding={"label": "Confirm"},
        )
    )
    context = build_action_context(cmap, request_type="goal_spec", allowed_actions=["click", "type"])

    result = PlanValidator().validate(
        context,
        [PrimitiveAction("type", affordance_id="dom_confirm", value="not valid for a button")],
    )

    assert not result.valid
    assert result.errors == ["action type is incompatible with affordance dom_confirm: expected click"]


def test_plan_validator_blocks_ordinary_actions_when_conflicts_are_unresolved():
    cmap = CognitiveMap(task_id="task_plan_validator_conflict")
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_confirm",
            source="dom",
            entity_id="booking_form",
            action_name="confirm",
            action_type="click",
            confidence=0.9,
            grounding={"label": "Confirm"},
        )
    )
    cmap.add_conflict(
        Conflict(
            id="booking.status",
            conflict_type="status_mismatch",
            sources=["dom", "wot"],
            description="Booking state disagrees.",
            conflict_mass=1.0,
            severity="high",
        )
    )
    context = build_action_context(cmap, request_type="goal_spec")
    validator = PlanValidator()

    blocked = validator.validate(context, [PrimitiveAction("click", affordance_id="dom_confirm")])
    allowed = validator.validate(context, [PrimitiveAction("ask_user", expected_effect="resolve conflict")])

    assert not blocked.valid
    assert blocked.errors == ["unresolved conflicts block action: click"]
    assert allowed.valid
