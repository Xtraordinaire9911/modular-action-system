"""Bounded System-2 planner wrapper.

This module is intentionally not an open-ended natural-language planner. It
delegates to the affordance-level controller by default and keeps any future LLM
planner behind an explicit, validated boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.runtime.action_context import ActionContext
from src.runtime.affordance_controller import AffordanceController, PrimitivePlan


@dataclass
class System2Planner:
    controller: AffordanceController
    mode: str = "deterministic_reflex"
    uses_llm: bool = False

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan:
        return self.controller.plan(context, goal_id=goal_id, goal_state=goal_state, parameters=parameters or {})
