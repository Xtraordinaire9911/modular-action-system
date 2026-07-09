"""Validation for primitive action plans before execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.runtime.action_context import ActionContext
from src.runtime.primitive_action import PrimitiveAction

_CONFLICT_SAFE_ACTIONS = frozenset(["ask_user", "done", "wait"])
_AFFORDANCE_OPTIONAL_ACTIONS = frozenset(["ask_user", "done", "wait", "scroll"])


@dataclass(frozen=True)
class PlanValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class PlanValidator:
    """Reject planner output that tries to bypass the runtime boundary."""

    def validate(self, context: ActionContext, actions: list[PrimitiveAction]) -> PlanValidationResult:
        errors: list[str] = []
        affordance_ids = {affordance.id for affordance in context.affordances}
        allowed_actions = set(context.allowed_actions)
        has_conflicts = bool(context.unresolved_conflicts)

        for action in actions:
            if action.action not in allowed_actions:
                errors.append(f"action type is not allowed: {action.action}")
                continue
            if has_conflicts and action.action not in _CONFLICT_SAFE_ACTIONS:
                errors.append(f"unresolved conflicts block action: {action.action}")
                continue
            if action.action not in _AFFORDANCE_OPTIONAL_ACTIONS and action.affordance_id not in affordance_ids:
                errors.append(f"unknown affordance_id: {action.affordance_id}")

        return PlanValidationResult(valid=not errors, errors=errors)
