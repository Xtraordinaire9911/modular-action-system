"""Environment-agnostic task planning contracts.

This module is deliberately not a web planner. It does not tokenize natural
language goals and it does not know about pages, carts, forms, rooms, devices,
or any benchmark. It only consumes declared runtime contracts:

* a structured goal/spec supplied by an upstream intent layer or test fixture
* sanitized affordances from the CognitiveMap
* explicit affordance metadata such as ``binds_parameter`` and ``achieves``

If those declarations are missing, the correct runtime behavior is to escalate
or route to a future LLM planner, not to hide benchmark-specific keyword rules
inside the action system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.runtime.action_context import ActionContext
from src.runtime.cognitive_map import RuntimeAffordance
from src.runtime.primitive_action import PrimitiveAction, PrimitiveActionType

TaskStepKind = Literal["bind_parameter", "complete_goal", "read_state", "clarify"]


@dataclass(frozen=True)
class RuntimeTaskStep:
    id: str
    kind: TaskStepKind
    action: PrimitiveAction
    source: str = ""
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTaskPlan:
    goal_id: str
    goal_state: str
    steps: list[RuntimeTaskStep] = field(default_factory=list)
    requires_escalation: bool = False
    reason: str = ""

    @property
    def actions(self) -> list[PrimitiveAction]:
        return [step.action for step in self.steps]


class DeclarativeRuntimeTaskPlanner:
    """Build a primitive task plan from explicit affordance declarations."""

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        self._min_confidence = min_confidence

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str,
        goal_state: str,
        parameters: dict[str, Any] | None = None,
    ) -> RuntimeTaskPlan:
        if context.unresolved_conflicts:
            return RuntimeTaskPlan(
                goal_id=goal_id,
                goal_state=goal_state,
                steps=[
                    RuntimeTaskStep(
                        id="clarify_conflict",
                        kind="clarify",
                        action=PrimitiveAction(
                            "ask_user",
                            expected_effect="resolve unresolved perceptual conflicts",
                        ),
                        reason="unresolved conflicts block environment-agnostic planning",
                    )
                ],
                requires_escalation=True,
                reason="unresolved conflicts block environment-agnostic planning",
            )

        parameters = parameters or {}
        steps: list[RuntimeTaskStep] = []
        used_affordance_ids: set[str] = set()
        missing: list[str] = []

        for name, value in parameters.items():
            affordance = self._find_parameter_affordance(context.affordances, name, used_affordance_ids)
            if affordance is None:
                missing.append(name)
                continue
            primitive = primitive_for_affordance(affordance)
            if primitive not in {"type", "select", "invoke"}:
                missing.append(name)
                continue
            used_affordance_ids.add(affordance.id)
            steps.append(
                RuntimeTaskStep(
                    id=f"bind_{name}",
                    kind="bind_parameter",
                    source=affordance.source,
                    action=PrimitiveAction(
                        primitive,
                        affordance_id=affordance.id,
                        value=value,
                        expected_effect=f"{name} == {value!r}",
                    ),
                    reason=f"affordance declares binding for parameter '{name}'",
                    metadata={"parameter": name},
                )
            )

        if missing:
            reason = "; ".join(f"no declared affordance binding for parameter '{name}'" for name in missing)
            return RuntimeTaskPlan(
                goal_id=goal_id,
                goal_state=goal_state,
                steps=[
                    *steps,
                    RuntimeTaskStep(
                        id="clarify_missing_bindings",
                        kind="clarify",
                        action=PrimitiveAction(
                            "ask_user",
                            expected_effect=f"provide affordance bindings for: {', '.join(missing)}",
                        ),
                        reason=reason,
                    ),
                ],
                requires_escalation=True,
                reason=reason,
            )

        completion = self._find_completion_affordance(context.affordances, goal_id, goal_state, used_affordance_ids)
        if completion is not None:
            steps.append(
                RuntimeTaskStep(
                    id="complete_goal",
                    kind="complete_goal",
                    source=completion.source,
                    action=PrimitiveAction(
                        primitive_for_affordance(completion),
                        affordance_id=completion.id,
                        expected_effect=goal_state,
                    ),
                    reason="affordance declares goal completion effect",
                    metadata={"goal_id": goal_id, "goal_state": goal_state},
                )
            )

        if not steps:
            return RuntimeTaskPlan(
                goal_id=goal_id,
                goal_state=goal_state,
                steps=[
                    RuntimeTaskStep(
                        id="clarify_no_plan",
                        kind="clarify",
                        action=PrimitiveAction("ask_user", expected_effect="provide declarative plan metadata"),
                        reason="no declarative parameter or completion affordance matched",
                    )
                ],
                requires_escalation=True,
                reason="no declarative parameter or completion affordance matched",
            )

        return RuntimeTaskPlan(goal_id=goal_id, goal_state=goal_state, steps=steps)

    def _find_parameter_affordance(
        self,
        affordances: list[RuntimeAffordance],
        parameter_name: str,
        used_affordance_ids: set[str],
    ) -> RuntimeAffordance | None:
        candidates = [
            affordance
            for affordance in affordances
            if affordance.id not in used_affordance_ids
            and affordance.confidence >= self._min_confidence
            and _declares_parameter_binding(affordance, parameter_name)
        ]
        return _highest_confidence(candidates)

    def _find_completion_affordance(
        self,
        affordances: list[RuntimeAffordance],
        goal_id: str,
        goal_state: str,
        used_affordance_ids: set[str],
    ) -> RuntimeAffordance | None:
        candidates = [
            affordance
            for affordance in affordances
            if affordance.id not in used_affordance_ids
            and affordance.confidence >= self._min_confidence
            and _declares_goal_completion(affordance, goal_id, goal_state)
        ]
        return _highest_confidence(candidates)


def primitive_for_affordance(affordance: RuntimeAffordance) -> PrimitiveActionType:
    action_type = affordance.action_type.lower()
    action_name = affordance.action_name.lower()
    if action_type in {"input", "textbox", "text", "field", "type"} or action_name in {"type", "input"}:
        return "type"
    if action_type in {"select", "dropdown"} or action_name == "select":
        return "select"
    if action_type in {"property", "sensor", "read"}:
        return "read"
    if action_type in {"action", "invoke"} or action_name.startswith("set_"):
        return "invoke"
    return "click"


def _declares_parameter_binding(affordance: RuntimeAffordance, parameter_name: str) -> bool:
    declared = _as_string_set(
        affordance.grounding.get("binds_parameter"),
        affordance.grounding.get("binds_parameters"),
        affordance.grounding.get("parameter"),
        affordance.grounding.get("parameters"),
        affordance.grounding.get("accepts_parameter"),
        affordance.grounding.get("accepts_parameters"),
    )
    if parameter_name in declared:
        return True
    schema = affordance.input_schema or {}
    properties = schema.get("properties")
    return isinstance(properties, dict) and parameter_name in properties


def _declares_goal_completion(affordance: RuntimeAffordance, goal_id: str, goal_state: str) -> bool:
    declared = _as_string_set(
        affordance.grounding.get("completion_for"),
        affordance.grounding.get("goal_id"),
        affordance.grounding.get("goal_ids"),
        affordance.grounding.get("achieves"),
        affordance.grounding.get("achieves_goal"),
        affordance.grounding.get("effects"),
    )
    return goal_id in declared or goal_state in declared


def _as_string_set(*values: Any) -> set[str]:
    strings: set[str] = set()
    for value in values:
        if isinstance(value, str):
            strings.add(value)
        elif isinstance(value, list | tuple | set):
            strings.update(item for item in value if isinstance(item, str))
    return strings


def _highest_confidence(affordances: list[RuntimeAffordance]) -> RuntimeAffordance | None:
    if not affordances:
        return None
    return max(affordances, key=lambda affordance: affordance.confidence)
