"""Runtime-owned interface to an externally implemented Agent/Planner."""

from __future__ import annotations

from typing import Any, Protocol

from src.runtime.action_context import ActionContext
from src.runtime.affordance_controller import PrimitivePlan


class PlannerPort(Protocol):
    """Return a typed primitive proposal; Runtime remains the execution authority."""

    def plan(
        self,
        context: ActionContext,
        *,
        goal_id: str = "",
        goal_state: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> PrimitivePlan: ...


__all__ = ["PlannerPort"]
