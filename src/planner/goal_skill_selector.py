"""Select and instantiate a reusable Skill from a structured goal.

This is the small bridge between the intent boundary (``GoalSpec``) and the
durable Skill Library.  A Skill can be selected by its exact identifier or by
one of its declared semantic ``goal_states``.  The latter is what lets a stable
intent such as ``room_booked`` select a reusable Skill such as
``confirm_booking``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.contracts.types import SkillCall, SkillTuple
from src.runtime.goal_spec import GoalSpec
from src.skill_library.library import SkillLibrary, SkillLibraryError


class GoalSkillSelectionError(ValueError):
    """Raised when a GoalSpec cannot safely become a SkillCall."""


@dataclass(frozen=True)
class GoalSkillSelection:
    """The catalog Skill and the concrete call created for one goal."""

    skill_tuple: SkillTuple
    skill_call: SkillCall


class GoalSkillSelector:
    """Match a stable goal capability to a Skill and validate parameters."""

    def __init__(self, skill_library: SkillLibrary) -> None:
        self.skill_library = skill_library

    def match(self, goal_spec: GoalSpec) -> SkillTuple | None:
        """Return the one Skill declaring this goal, or ``None`` if absent.

        Exact Skill IDs win so existing callers keep their behavior.  Semantic
        aliases are intentionally stored on the Skill contract rather than in
        an intent-specific if/else table.
        """

        try:
            return self.skill_library.get(goal_spec.goal_id)
        except SkillLibraryError:
            pass

        semantic_keys = {goal_spec.goal_id, goal_spec.goal_state}
        matches = [skill for skill in self.skill_library.all() if semantic_keys.intersection(skill.goal_states)]
        if len(matches) > 1:
            ids = ", ".join(sorted(skill.skill_id for skill in matches))
            raise GoalSkillSelectionError(f"multiple Skills match goal {goal_spec.goal_id!r}: {ids}")
        return matches[0] if matches else None

    def select(self, goal_spec: GoalSpec) -> GoalSkillSelection:
        goal_errors = goal_spec.validate()
        if goal_errors:
            details = "; ".join(goal_errors)
            raise GoalSkillSelectionError(f"invalid GoalSpec: {details}")

        skill = self.match(goal_spec)
        if skill is None:
            available = ", ".join(sorted(self.skill_library.ids())) or "none"
            raise GoalSkillSelectionError(
                f"no Skill matches goal_id {goal_spec.goal_id!r}; available skills: {available}"
            )

        params = _bind_default_parameters(skill, goal_spec.parameters)
        _validate_parameters(skill, params)

        return GoalSkillSelection(
            skill_tuple=skill,
            skill_call=SkillCall(
                skill_id=skill.skill_id,
                params=params,
                required_postconditions=list(skill.postconditions),
                preferred_backends=list(skill.preferred_backends),
            ),
        )


def select_goal_skill(goal_spec: GoalSpec, skill_library: SkillLibrary) -> GoalSkillSelection:
    """Convenience function for callers that do not need to retain a selector."""

    return GoalSkillSelector(skill_library).select(goal_spec)


_TYPE_ALIASES: dict[str, type[object]] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
}


def _bind_default_parameters(skill: SkillTuple, supplied: dict[str, Any]) -> dict[str, Any]:
    """Instantiate optional Skill defaults without changing the GoalSpec.

    The GoalSpec should contain what the person actually asked for. Reusable
    execution defaults belong to the Skill contract, so a demo runner does not
    have to secretly rewrite the goal before handing it to the runtime.
    """

    params = dict(supplied)
    for name, rule in skill.parameters_schema.items():
        if name not in params and isinstance(rule, dict) and "default" in rule:
            params[name] = rule["default"]
    return params


def _validate_parameters(skill: SkillTuple, params: dict[str, Any]) -> None:
    schema = skill.parameters_schema
    if not isinstance(schema, dict):
        raise GoalSkillSelectionError(f"Skill {skill.skill_id!r} has an invalid parameters_schema")

    expected_names = set(schema)
    unexpected = sorted(set(params) - expected_names)
    if unexpected:
        names = ", ".join(unexpected)
        raise GoalSkillSelectionError(f"unexpected parameter(s) for Skill {skill.skill_id!r}: {names}")

    for name, rule in schema.items():
        expected_type, required = _parse_type_rule(skill.skill_id, name, rule)
        if name not in params:
            if required:
                raise GoalSkillSelectionError(f"missing required parameter {name!r} for Skill {skill.skill_id!r}")
            continue

        value = params[name]
        if not _has_expected_type(value, expected_type):
            raise GoalSkillSelectionError(
                f"parameter {name!r} for Skill {skill.skill_id!r} must be "
                f"{expected_type.__name__}, got {type(value).__name__}"
            )


def _parse_type_rule(skill_id: str, parameter_name: str, rule: Any) -> tuple[type[object], bool]:
    required = True
    declared_type = rule
    if isinstance(rule, dict):
        declared_type = rule.get("type")
        required = bool(rule.get("required", True))

    if declared_type in (str, int, float, bool):
        return declared_type, required
    if isinstance(declared_type, str) and declared_type.lower() in _TYPE_ALIASES:
        return _TYPE_ALIASES[declared_type.lower()], required

    raise GoalSkillSelectionError(
        f"parameter {parameter_name!r} for Skill {skill_id!r} has unsupported schema type {declared_type!r}; "
        "supported types are str, int, float, and bool"
    )


def _has_expected_type(value: Any, expected_type: type[object]) -> bool:
    # bool is an int subclass in Python, but a boolean should not satisfy an
    # integer Skill parameter.  Keep all four simple schema types explicit.
    return type(value) is expected_type
