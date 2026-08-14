"""Carry out a device goal, and confirm each property by reading it back.

Resolution (:mod:`src.planner.device_binding`) answers *where* to write. This
answers whether the write did anything, which is a separate question and the one
that matters: the smart-room servient can answer ``204 No Content`` to a write
that changed nothing, and a run that treats the status code as the outcome will
report a room it never prepared.

So every write here is followed by a read of the same property, and the goal is
met only where the value that comes back is the value that was asked for. That is
the same rule the DOM side already follows - re-observe rather than trust the
executor - applied to devices.

For a composite goal such as ``room_prepared`` the parts are verified
**individually**. A composite that wrote three properties and confirmed two is
not two thirds met; it is not met, and the report says which part failed. Parts
the environment cannot offer at all are reported as skipped when the declaration
marks them optional, and fail the goal when it does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.planner.device_binding import (
    CompositeDeviceGoal,
    CompositePart,
    DeviceResolutionError,
    ResolvedDeviceTarget,
    resolve_composite_goal,
)

# Numbers come back from a servient as int or float, and a percentage written as
# 30 may read back as 30.0. Comparing with a tolerance is not laxity: it is the
# difference between verifying the value and verifying its Python type.
NUMERIC_TOLERANCE = 1e-6


class DeviceIO(Protocol):
    """The part of a WoT executor this needs: write one property, read one back."""

    def write_state(self, source: Any, value: Any) -> None: ...
    def read_state(self, source: Any) -> Any: ...


def _as_bool(value: Any) -> bool | None:
    """The boolean this value states, or None if it does not state one."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def values_match(wanted: Any, observed: Any) -> bool:
    """Whether the property now holds what was asked for.

    ``observed`` may be the raw property value or a small JSON object wrapping
    it, since servients differ on that; both are accepted, and anything else is
    a mismatch rather than an optimistic guess.
    """
    if isinstance(observed, dict):
        for key in ("value", "result", "data"):
            if key in observed:
                observed = observed[key]
                break
    if isinstance(wanted, bool) or isinstance(observed, bool):
        # Deliberately not truthiness. A property asked to be True and reading 2
        # has not been set to True, and treating every non-zero number as True
        # would report that as verified.
        left, right = _as_bool(wanted), _as_bool(observed)
        return left is not None and left == right
    if isinstance(wanted, (int, float)) and isinstance(observed, (int, float)):
        return abs(float(wanted) - float(observed)) <= NUMERIC_TOLERANCE
    return str(wanted).strip().lower() == str(observed).strip().lower()


@dataclass
class PartOutcome:
    """What happened to one property: attempted, confirmed, or why not."""

    goal_state: str
    required: bool
    thing_id: str = ""
    thing_title: str = ""  # the recognisable name; thing_id is a UUID
    property: str = ""
    wanted: Any = None
    observed: Any = None
    written: bool = False
    verified: bool = False
    skipped_reason: str = ""  # set when the part was never attempted
    error: str = ""

    # A method rather than a @property: the WoT field above is called "property",
    # which shadows the builtin for the rest of this class body. Renaming the
    # field would be worse - it is the Thing Description's own word for it.
    def blocks_goal(self) -> bool:
        """True when this part is the reason the composite cannot be claimed."""
        return self.required and not self.verified

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_state": self.goal_state,
            "required": self.required,
            "thing_id": self.thing_id,
            "thing_title": self.thing_title,
            "property": self.property,
            "wanted": self.wanted,
            "observed": self.observed,
            "written": self.written,
            "verified": self.verified,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
        }


@dataclass
class GoalOutcome:
    """The composite result, and every part that produced it."""

    goal_state: str
    parts: list[PartOutcome] = field(default_factory=list)
    discovered_things: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """Met only when every required part was confirmed by re-reading it."""
        required = [p for p in self.parts if p.required]
        return bool(required) and all(p.verified for p in required)

    @property
    def unmet(self) -> list[PartOutcome]:
        return [p for p in self.parts if p.blocks_goal()]

    def summary(self) -> str:
        confirmed = sum(1 for p in self.parts if p.verified)
        skipped = [p.goal_state for p in self.parts if p.skipped_reason and not p.required]
        text = f"{confirmed}/{len(self.parts)} properties confirmed by reading them back"
        if skipped:
            text += f"; skipped (not in this room): {', '.join(skipped)}"
        if self.unmet:
            text += f"; failed: {', '.join(p.goal_state for p in self.unmet)}"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_state": self.goal_state,
            "verified": self.verified,
            "summary": self.summary(),
            "discovered_things": list(self.discovered_things),
            "parts": [p.to_dict() for p in self.parts],
        }


def _attempt(io: DeviceIO, part: CompositePart, target: ResolvedDeviceTarget) -> PartOutcome:
    outcome = PartOutcome(
        goal_state=part.goal_state,
        required=part.required,
        thing_id=target.thing_id,
        thing_title=target.thing_title,
        property=target.property,
        wanted=target.value,
    )
    if target.source is None:
        outcome.error = "resolution returned no state source to write through"
        return outcome
    try:
        io.write_state(target.source, target.value)
        outcome.written = True
    except Exception as exc:  # a failed write is a result, not a crash
        outcome.error = f"write failed: {type(exc).__name__}: {exc}"
        return outcome
    try:
        outcome.observed = io.read_state(target.source)
    except Exception as exc:
        outcome.error = f"read-back failed: {type(exc).__name__}: {exc}"
        return outcome
    outcome.verified = values_match(target.value, outcome.observed)
    if not outcome.verified:
        # The write was accepted and the value did not change. This is the
        # silent-device-write class of failure, and naming it here is what stops
        # it being reported as success.
        outcome.error = f"accepted the write but still reads {outcome.observed!r}"
    return outcome


def pursue_composite_goal(
    goal: CompositeDeviceGoal,
    models: list[Any],
    parameters: dict[str, Any],
    io: DeviceIO,
) -> GoalOutcome:
    """Write every part of ``goal`` and verify each one independently."""
    resolved = resolve_composite_goal(goal, models, parameters)
    outcome = GoalOutcome(goal_state=goal.goal_state)
    for part, resolution in resolved:
        if isinstance(resolution, DeviceResolutionError):
            outcome.discovered_things = outcome.discovered_things or list(resolution.discovered_things)
            outcome.parts.append(
                PartOutcome(
                    goal_state=part.goal_state,
                    required=part.required,
                    skipped_reason=resolution.reason,
                    error=resolution.detail,
                )
            )
            continue
        outcome.discovered_things = outcome.discovered_things or list(resolution.discovered_things)
        outcome.parts.append(_attempt(io, part, resolution))
    return outcome


__all__ = [
    "DeviceIO",
    "GoalOutcome",
    "NUMERIC_TOLERANCE",
    "PartOutcome",
    "pursue_composite_goal",
    "values_match",
]
