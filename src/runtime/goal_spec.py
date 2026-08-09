"""Structured goal boundary for runtime-controlled action planning.

GoalSpec is the handoff point from an upstream user-intent layer into this
action system. The runtime accepts explicit goal state, parameters, and safety
constraints; it does not infer broad natural-language intent here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GoalSource = Literal["user_intent_parser", "fixture", "demo", "benchmark", "manual"]


@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    goal_state: str
    parameters: dict[str, Any] = field(default_factory=dict)
    source: GoalSource = "manual"
    description: str = ""
    safety_constraints: list[str] = field(default_factory=list)
    success_evidence: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.goal_id.strip():
            errors.append("goal_id must be non-empty")
        if not self.goal_state.strip():
            errors.append("goal_state must be non-empty")
        if not isinstance(self.parameters, dict):
            errors.append("parameters must be a dictionary")
        return errors
