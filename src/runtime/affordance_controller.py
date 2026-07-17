"""Bounded affordance-level controller for no-durable-skill cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.runtime.action_context import ActionContext
from src.runtime.cognitive_map import RuntimeAffordance
from src.runtime.primitive_action import PrimitiveAction, PrimitiveActionType


@dataclass(frozen=True)
class PrimitivePlan:
    actions: list[PrimitiveAction] = field(default_factory=list)
    requires_escalation: bool = False
    reason: str = ""


class AffordanceController:
    """Create a typed primitive plan from a sanitized ActionContext.

    This controller is intentionally conservative. It handles clear form-like
    goals by binding known parameters to matching input affordances and then
    choosing a goal-relevant submit/click/invoke affordance. It does not infer
    arbitrary user intent and does not emit raw selectors.
    """

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        self._min_confidence = min_confidence

    def plan(
        self,
        context: ActionContext,
        *,
        goal_state: str,
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan:
        if context.unresolved_conflicts:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="resolve unresolved perceptual conflicts")],
                requires_escalation=True,
                reason="unresolved conflicts block affordance-level planning",
            )

        parameters = parameters or {}
        actions: list[PrimitiveAction] = []
        missing: list[str] = []
        used: set[str] = set()

        for name, value in parameters.items():
            affordance = self._find_parameter_affordance(context.affordances, name, used)
            if affordance is None:
                missing.append(name)
                continue
            primitive = _primitive_for_affordance(affordance)
            if primitive not in {"type", "select"}:
                missing.append(name)
                continue
            used.add(affordance.id)
            actions.append(
                PrimitiveAction(
                    primitive,
                    affordance_id=affordance.id,
                    value=value,
                    expected_effect=f"{name} == {value!r}",
                )
            )

        if missing:
            return PrimitivePlan(
                actions=[
                    PrimitiveAction(
                        "ask_user",
                        expected_effect=f"clarify missing affordances for: {', '.join(missing)}",
                    )
                ],
                requires_escalation=True,
                reason="; ".join(f"no matching affordance for parameter '{name}'" for name in missing),
            )

        completion = self._find_completion_affordance(context.affordances, goal_state, used)
        if completion is not None:
            actions.append(
                PrimitiveAction(
                    _primitive_for_affordance(completion),
                    affordance_id=completion.id,
                    expected_effect=goal_state,
                )
            )
            return PrimitivePlan(actions=actions)

        if not actions:
            return PrimitivePlan(
                actions=[PrimitiveAction("ask_user", expected_effect="clarify executable affordance")],
                requires_escalation=True,
                reason="no executable affordance matched the goal",
            )

        return PrimitivePlan(actions=actions, reason="parameter actions planned without a completion affordance")

    def _find_parameter_affordance(
        self,
        affordances: list[RuntimeAffordance],
        name: str,
        used: set[str],
    ) -> RuntimeAffordance | None:
        candidates = [
            affordance
            for affordance in affordances
            if affordance.id not in used
            and affordance.confidence >= self._min_confidence
            and _primitive_for_affordance(affordance) in {"type", "select"}
        ]
        return _best_label_match(candidates, name)

    def _find_completion_affordance(
        self,
        affordances: list[RuntimeAffordance],
        goal_state: str,
        used: set[str],
    ) -> RuntimeAffordance | None:
        candidates = [
            affordance
            for affordance in affordances
            if affordance.id not in used
            and affordance.confidence >= self._min_confidence
            and _primitive_for_affordance(affordance) in {"click", "invoke"}
        ]
        return _best_label_match(candidates, goal_state) or (candidates[0] if len(candidates) == 1 else None)


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


def _best_label_match(affordances: list[RuntimeAffordance], query: str) -> RuntimeAffordance | None:
    query_tokens = _tokens(query)
    best: RuntimeAffordance | None = None
    best_score = 0
    for affordance in affordances:
        label = str(affordance.grounding.get("label", affordance.action_name))
        haystack = _tokens(" ".join([affordance.id, affordance.action_name, label]))
        score = len(query_tokens & haystack)
        if score > best_score:
            best = affordance
            best_score = score
    return best


def _tokens(text: str) -> set[str]:
    normalized = text.replace("_", " ").replace("-", " ").replace(".", " ").lower()
    return {token for token in normalized.split() if token}
