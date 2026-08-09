"""Typed primitive actions for bounded no-durable-skill execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PrimitiveActionType = Literal["click", "type", "select", "invoke", "read", "scroll", "wait", "ask_user", "done"]


@dataclass(frozen=True)
class PrimitiveAction:
    """A planner proposal that still requires runtime validation before execution."""

    action: PrimitiveActionType
    affordance_id: str = ""
    value: Any | None = None
    expected_effect: str = ""
