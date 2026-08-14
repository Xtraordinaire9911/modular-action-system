import pytest

from src.contracts.types import Condition, SkillTuple
from src.planner.goal_skill_selector import GoalSkillSelectionError, GoalSkillSelector, select_goal_skill
from src.runtime.goal_spec import GoalSpec
from src.skill_library import SkillLibrary


def _skill(skill_id: str, parameters_schema: dict[str, object]) -> SkillTuple:
    return SkillTuple(
        skill_id=skill_id,
        description="Demo skill",
        parameters_schema=parameters_schema,
        preconditions=[],
        postconditions=[Condition("booking.confirmed == true")],
        allowed_backends=["dom", "visual"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=5_000,
        safety_level="medium",
        irreversible=False,
    )


def _goal(goal_id: str = "confirm_booking", **parameters: object) -> GoalSpec:
    return GoalSpec(
        goal_id=goal_id,
        goal_state="booking.confirmed == true",
        parameters=parameters,
        source="demo",
    )


def test_selects_matching_skill_and_instantiates_call() -> None:
    skill = _skill("confirm_booking", {"room": "str", "time": "str"})
    selection = GoalSkillSelector(SkillLibrary([skill])).select(_goal(room="A", time="14:00"))

    assert selection.skill_tuple is skill
    assert selection.skill_call.skill_id == "confirm_booking"
    assert selection.skill_call.params == {"room": "A", "time": "14:00"}
    assert selection.skill_call.required_postconditions == skill.postconditions
    assert selection.skill_call.preferred_backends == ["dom"]


def test_convenience_function_uses_same_selection_path() -> None:
    skill = _skill("confirm_booking", {"room": "str"})

    selection = select_goal_skill(_goal(room="A"), SkillLibrary([skill]))

    assert selection.skill_tuple is skill
    assert selection.skill_call.params == {"room": "A"}


def test_unknown_goal_lists_available_skills() -> None:
    library = SkillLibrary([_skill("confirm_booking", {})])

    with pytest.raises(
        GoalSkillSelectionError,
        match="no Skill matches goal_id 'missing'.*available skills: confirm_booking",
    ):
        GoalSkillSelector(library).select(_goal("missing"))


def test_missing_required_parameter_is_rejected() -> None:
    library = SkillLibrary([_skill("confirm_booking", {"room": "str", "time": "str"})])

    with pytest.raises(GoalSkillSelectionError, match="missing required parameter 'time'"):
        GoalSkillSelector(library).select(_goal(room="A"))


@pytest.mark.parametrize(
    ("schema_type", "value", "expected_name"),
    [
        ("str", 7, "str"),
        ("int", "7", "int"),
        ("float", 7, "float"),
        ("bool", 1, "bool"),
    ],
)
def test_simple_parameter_types_are_checked(schema_type: str, value: object, expected_name: str) -> None:
    library = SkillLibrary([_skill("typed_goal", {"value": schema_type})])

    with pytest.raises(GoalSkillSelectionError, match=rf"must be {expected_name}"):
        GoalSkillSelector(library).select(_goal("typed_goal", value=value))


def test_each_supported_simple_type_can_be_selected() -> None:
    skill = _skill(
        "typed_goal",
        {"text": "str", "count": "int", "ratio": "float", "enabled": "bool"},
    )
    goal = _goal("typed_goal", text="hello", count=2, ratio=0.5, enabled=True)

    selection = GoalSkillSelector(SkillLibrary([skill])).select(goal)

    assert selection.skill_call.params == {"text": "hello", "count": 2, "ratio": 0.5, "enabled": True}


def test_optional_json_style_schema_parameter_may_be_omitted() -> None:
    skill = _skill(
        "confirm_booking",
        {"room": {"type": "string"}, "note": {"type": "string", "required": False}},
    )

    selection = GoalSkillSelector(SkillLibrary([skill])).select(_goal(room="A"))

    assert selection.skill_call.params == {"room": "A"}


def test_unexpected_parameter_is_rejected() -> None:
    library = SkillLibrary([_skill("confirm_booking", {"room": "str"})])

    with pytest.raises(GoalSkillSelectionError, match="unexpected parameter.*extra"):
        GoalSkillSelector(library).select(_goal(room="A", extra="not declared"))


def test_invalid_goal_spec_is_rejected_before_library_lookup() -> None:
    invalid = GoalSpec(goal_id="", goal_state="", parameters={})

    with pytest.raises(GoalSkillSelectionError, match="invalid GoalSpec:.*goal_id.*goal_state"):
        GoalSkillSelector(SkillLibrary()).select(invalid)


def test_unsupported_schema_type_has_clear_error() -> None:
    library = SkillLibrary([_skill("confirm_booking", {"room": "list"})])

    with pytest.raises(GoalSkillSelectionError, match="supported types are str, int, float, and bool"):
        GoalSkillSelector(library).select(_goal(room="A"))
