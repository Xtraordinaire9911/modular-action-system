"""Bounded affordance-level controller for no-durable-skill cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.runtime.action_context import ActionContext
from src.runtime.cognitive_map import RuntimeAffordance
from src.runtime.primitive_action import PrimitiveAction, PrimitiveActionType
from src.runtime.task_planner import DeclarativeRuntimeTaskPlanner, RuntimeTaskPlan


@dataclass(frozen=True)
class PrimitivePlan:
    actions: list[PrimitiveAction] = field(default_factory=list)
    requires_escalation: bool = False
    reason: str = ""


class AffordanceController:
    """Create a typed primitive plan from a sanitized ActionContext.

    This controller is intentionally conservative. It consumes declared
    affordance semantics instead of environment-specific keywords: parameters
    bind to affordances that declare they accept them, and completion actions
    must declare the goal/effect they achieve. It does not infer arbitrary user
    intent and does not emit raw selectors.
    """

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        self._min_confidence = min_confidence
        self._task_planner = DeclarativeRuntimeTaskPlanner(min_confidence=min_confidence)

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str = "",
        goal_state: str,
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan:
        if context.unresolved_conflicts:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="resolve unresolved perceptual conflicts")],
                requires_escalation=True,
                reason="unresolved conflicts block affordance-level planning",
            )

        if context.failure is not None:
            return self._plan_after_failure(context)

        task_plan = self._task_planner.plan(
            context,
            goal_id=goal_id,
            goal_state=goal_state,
            parameters=parameters or {},
        )
        if task_plan.requires_escalation:
            return PrimitivePlan(
                actions=task_plan.actions,
                requires_escalation=True,
                reason=task_plan.reason,
            )
        return self.plan_task(context, task_plan)

    def _plan_after_failure(self, context: ActionContext) -> PrimitivePlan:
        """Choose semantic recovery through the normal planner boundary.

        This deterministic controller is the non-model fallback of the existing
        System-2 planner. Runtime supplies facts; it does not select this action.
        """

        failure = context.failure
        assert failure is not None
        candidates: list[tuple[int, float, RuntimeAffordance, str]] = []
        for affordance in context.affordances:
            grounding = affordance.grounding
            role = str(grounding.get("recovery_role") or "")
            postcondition = str(grounding.get("recovery_postcondition") or "")
            if role not in {"clear_obstruction", "restore_precondition"} or not postcondition:
                continue
            if grounding.get("recovery_safe") is not True:
                continue
            if grounding.get("idempotent") is not True or grounding.get("irreversible") is not False:
                continue
            if grounding.get("safety_level") != "low":
                continue
            relations = _string_values(grounding.get("remediates"))
            relation_strength = 0
            if failure.failed_affordance_id in relations:
                relation_strength = 2
            elif failure.failed_entity_id and failure.failed_entity_id in relations:
                relation_strength = 1
            if relation_strength:
                candidates.append((relation_strength, affordance.confidence, affordance, postcondition))

        if not candidates:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="no safe observed recovery plan is available")],
                requires_escalation=True,
                reason="failure requires semantic replanning but no safe, verifiable recovery affordance was observed",
            )

        candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
        best = candidates[0]
        if len(candidates) > 1 and best[:2] == candidates[1][:2]:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="disambiguate recovery affordances")],
                requires_escalation=True,
                reason="multiple equally supported recovery affordances remain ambiguous",
            )
        _, _, affordance, postcondition = best
        return PrimitivePlan(
            actions=[
                PrimitiveAction(
                    _primitive_for_affordance(affordance),
                    affordance_id=affordance.id,
                    expected_effect=postcondition,
                )
            ],
            reason="existing Agent/Planner selected a safe observed precondition repair from fresh failure context",
        )

    def plan_task(self, context: ActionContext, task_plan: RuntimeTaskPlan) -> PrimitivePlan:
        _ = context
        actions: list[PrimitiveAction] = []
        for step in task_plan.steps:
            if step.kind == "clarify":
                return PrimitivePlan(
                    actions=[step.action],
                    requires_escalation=True,
                    reason=step.reason,
                )
            actions.append(step.action)
        if not actions:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="clarify executable affordance")],
                requires_escalation=True,
                reason="no executable affordance matched the goal",
            )
        return PrimitivePlan(actions=actions)

    def _find_parameter_affordance(
        self,
        affordances: list[RuntimeAffordance],
        name: str,
        used: set[str],
    ) -> RuntimeAffordance | None:
        return self._task_planner._find_parameter_affordance(affordances, name, used)

    def _find_completion_affordance(
        self,
        affordances: list[RuntimeAffordance],
        goal_state: str,
        used: set[str],
    ) -> RuntimeAffordance | None:
        return self._task_planner._find_completion_affordance(affordances, "", goal_state, used)


def _primitive_for_affordance(affordance: RuntimeAffordance) -> PrimitiveActionType:
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


def _string_values(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()
