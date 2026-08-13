"""Sanitized planning surface derived from CognitiveMap.

The ActionContext is the boundary between the runtime's internal state and any
bounded controller or optional System-2 planner. It exposes state assertions and
affordances, but strips raw selectors and backend-specific handles from the
planner-facing affordance grounding.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Literal

from src.runtime.cognitive_map import CognitiveMap, Conflict, RuntimeAffordance

RequestType = Literal["skill_call", "goal_spec", "primitive_action"]


class PlannerHandoff(str, Enum):
    NONE = "none"
    REPLAN_REQUIRED = "replan_required"


_SAFE_GROUNDING_KEYS = frozenset(
    [
        "label",
        "description",
        "role",
        "text",
        "mark_id",
        "thing_id",
        "parameter",
        "binds_parameter",
        "binds_parameters",
        "accepts_parameter",
        "accepts_parameters",
        "parameters",
        "achieves",
        "achieves_goal",
        "completion_for",
        "goal_id",
        "goal_ids",
        "effects",
        "observes",
        "idempotent",
        "irreversible",
        "safety_level",
        "recovery_role",
        "remediates",
        "compensates",
        "equivalent_to",
        "restores",
        "observes",
        "recovery_postcondition",
        "recovery_safe",
    ]
)
_DEFAULT_ALLOWED_ACTIONS = ["click", "type", "select", "invoke", "read", "scroll", "wait", "ask_user", "done"]


@dataclass(frozen=True)
class ActionContext:
    task_id: str
    request_type: RequestType
    state: dict[str, dict]
    affordances: list[RuntimeAffordance]
    unresolved_conflicts: list[Conflict]
    allowed_actions: list[str]
    safety_constraints: list[str]
    failure: FailureContext | None = None
    attempted_actions: list[AttemptedAction] = field(default_factory=list)
    remaining_steps: int | None = None
    remaining_retries: int | None = None


@dataclass(frozen=True)
class AttemptedAction:
    """Planner-visible history; contains semantic IDs, never backend handles."""

    action: str
    affordance_id: str
    expected_effect: str
    outcome: str
    transition_id: str = ""


@dataclass(frozen=True)
class FailureContext:
    """Fresh, typed evidence returned by Runtime to the existing Agent/Planner."""

    failed_action: str
    failed_affordance_id: str
    failed_entity_id: str
    expected_effect: str
    failure_boundary: str
    failure_type: str
    reason: str
    transition_id: str
    observation_state_id: str
    observation_request_id: str = ""


def build_action_context(
    cognitive_map: CognitiveMap,
    *,
    request_type: RequestType,
    allowed_actions: list[str] | None = None,
    safety_constraints: list[str] | None = None,
    failure: FailureContext | None = None,
    attempted_actions: list[AttemptedAction] | None = None,
    remaining_steps: int | None = None,
    remaining_retries: int | None = None,
) -> ActionContext:
    """Build a planner-facing snapshot without exposing raw DOM selectors."""

    return ActionContext(
        task_id=cognitive_map.task_id,
        request_type=request_type,
        state={
            "dom": dict(cognitive_map.page_state),
            "visual": dict(cognitive_map.visual_state),
            "wot": dict(cognitive_map.device_states),
        },
        affordances=[
            _sanitize_affordance(affordance)
            for affordance in cognitive_map.runtime_affordances.values()
            if not _is_runtime_overlay(affordance)
        ],
        unresolved_conflicts=list(cognitive_map.unresolved_conflicts()),
        allowed_actions=list(allowed_actions or _DEFAULT_ALLOWED_ACTIONS),
        safety_constraints=list(safety_constraints or []),
        failure=failure,
        attempted_actions=list(attempted_actions or []),
        remaining_steps=remaining_steps,
        remaining_retries=remaining_retries,
    )


def _sanitize_affordance(affordance: RuntimeAffordance) -> RuntimeAffordance:
    grounding = {
        key: value
        for key, value in affordance.grounding.items()
        if key in _SAFE_GROUNDING_KEYS and _is_safe_grounding_value(value)
    }
    if "label" not in grounding:
        grounding["label"] = affordance.action_name
    return replace(affordance, grounding=grounding)


def _is_runtime_overlay(affordance: RuntimeAffordance) -> bool:
    return bool(affordance.grounding.get("demo_overlay") or affordance.grounding.get("runtime_overlay"))


def _is_safe_grounding_value(value: object) -> bool:
    if isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list | tuple | set):
        return all(isinstance(item, str | int | float | bool) for item in value)
    return False
