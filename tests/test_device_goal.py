"""A composite device goal must be met per property, or not claimed at all.

The failure this guards against is specific and the smart-room servient can
produce it: a write is accepted, the property does not change, and a runner that
believes the status code reports a prepared room. Every test here is about the
difference between "the write was accepted" and "the value is now what was
asked for".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.planner.device_binding import (
    CompositeDeviceGoal,
    CompositePart,
    composite_goal_for,
    resolve_composite_goal,
)
from src.runtime.device_goal import pursue_composite_goal, values_match


# Structural stand-ins for the parsed TD types, matching tests/test_device_binding.py.
# Resolution reads these attributes and nothing else, so a fake keeps the test
# about the goal logic rather than about TD parsing.
@dataclass
class _Source:
    thing_id: str
    property: str
    href: str
    method: str
    read_only: bool


@dataclass
class _Model:
    thing_id: str
    state_sources: list[_Source] = field(default_factory=list)


def _thing(thing_id: str, prop: str, *, read_only: bool = False) -> _Model:
    return _Model(
        thing_id=thing_id,
        state_sources=[
            _Source(thing_id, prop, f"http://localhost:8080/{thing_id}/properties/{prop}", "PUT", read_only)
        ],
    )


def _room(*, with_blinds: bool = True, with_thermostat: bool = True) -> list[_Model]:
    things = [_thing("projector", "power"), _thing("lights", "brightness")]
    if with_blinds:
        things.append(_thing("blinds", "position"))
    if with_thermostat:
        things.append(_thing("thermostat", "targetTemperature"))
    return things


class FakeRoom:
    """A servient that stores what it is told, unless told to ignore a property."""

    def __init__(self, *, ignores: tuple[str, ...] = (), fails: tuple[str, ...] = ()) -> None:
        self.state: dict[str, Any] = {}
        self.writes: list[tuple[str, Any]] = []
        self._ignores = ignores
        self._fails = fails

    def write_state(self, source: Any, value: Any) -> None:
        key = f"{source.thing_id}.{source.property}"
        self.writes.append((key, value))
        if key in self._fails:
            raise RuntimeError("WoT PUT returned HTTP 503")
        if key in self._ignores:
            return  # accepted, changed nothing - the silent write
        self.state[key] = value

    def read_state(self, source: Any) -> Any:
        return self.state.get(f"{source.thing_id}.{source.property}")


# ── the declaration ─────────────────────────────────────────────────────────────


def test_room_prepared_is_declared_as_several_writes():
    goal = composite_goal_for("room_prepared")
    assert goal is not None
    assert len(goal.parts) >= 2
    assert [p.goal_state for p in goal.parts if p.required] == ["projector_on", "lighting_set"]


def test_every_part_names_a_goal_that_can_be_resolved():
    """A part pointing at a goal with no binding would fail only at run time."""
    from src.planner.device_binding import device_binding_for

    for part in composite_goal_for("room_prepared").parts:
        assert device_binding_for(part.goal_state) is not None, part.goal_state


def test_an_utterance_can_override_a_part_default():
    goal = composite_goal_for("room_prepared")
    lights = next(p for p in goal.parts if p.goal_state == "lighting_set")
    assert lights.value_from({}) == lights.value
    assert lights.value_from({"percent": 75}) == 75


def test_resolution_reports_the_missing_part_rather_than_the_nearest_device():
    resolved = resolve_composite_goal(composite_goal_for("room_prepared"), _room(with_blinds=False), {})
    by_goal = {part.goal_state: outcome for part, outcome in resolved}
    assert by_goal["blinds_set"].reason == "thing_not_discovered"
    assert by_goal["projector_on"].thing_id == "projector"


# ── execution and per-property verification ─────────────────────────────────────


def test_a_prepared_room_is_confirmed_property_by_property():
    room = FakeRoom()
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room)
    assert outcome.verified
    assert all(p.verified for p in outcome.parts)
    assert room.state["projector.power"] == "on"
    assert room.state["lights.brightness"] == 30


def test_a_write_that_changes_nothing_fails_the_goal():
    """The whole reason this module reads back: 204 is not evidence."""
    room = FakeRoom(ignores=("lights.brightness",))
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room)
    assert not outcome.verified
    lights = next(p for p in outcome.parts if p.goal_state == "lighting_set")
    assert lights.written and not lights.verified
    assert "still reads" in lights.error
    assert [p.goal_state for p in outcome.unmet] == ["lighting_set"]


def test_a_failed_write_is_reported_not_raised():
    room = FakeRoom(fails=("projector.power",))
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room)
    projector = next(p for p in outcome.parts if p.goal_state == "projector_on")
    assert not projector.written and not projector.verified
    assert "503" in projector.error
    assert not outcome.verified


def test_an_optional_part_this_room_lacks_is_skipped_and_said_so():
    room = FakeRoom()
    outcome = pursue_composite_goal(
        composite_goal_for("room_prepared"), _room(with_blinds=False, with_thermostat=False), {}, room
    )
    assert outcome.verified, "a room without blinds can still be prepared"
    skipped = [p for p in outcome.parts if p.skipped_reason]
    assert {p.goal_state for p in skipped} == {"blinds_set", "temperature_set"}
    assert "skipped (not in this room): blinds_set, temperature_set" in outcome.summary()


def test_a_required_part_this_room_lacks_fails_the_goal():
    room = FakeRoom()
    goal = CompositeDeviceGoal(
        goal_state="room_prepared",
        parts=(CompositePart(goal_state="projector_on", value="on"),),
    )
    outcome = pursue_composite_goal(goal, [_thing("lights", "brightness")], {}, room)
    assert not outcome.verified
    assert outcome.parts[0].skipped_reason == "thing_not_discovered"


def test_partly_prepared_is_not_prepared():
    """Two of three confirmed is not two thirds of a goal."""
    room = FakeRoom(ignores=("projector.power",))
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room)
    assert sum(1 for p in outcome.parts if p.verified) == 3
    assert not outcome.verified
    assert "failed: projector_on" in outcome.summary()


def test_the_report_names_what_was_discovered():
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, FakeRoom())
    assert "projector" in outcome.discovered_things and "lights" in outcome.discovered_things
    assert outcome.to_dict()["verified"] is True


# ── comparison ──────────────────────────────────────────────────────────────────


def test_a_number_written_as_int_and_read_as_float_still_matches():
    assert values_match(30, 30.0)
    assert not values_match(30, 31)


def test_a_value_wrapped_by_the_servient_is_unwrapped():
    assert values_match("on", {"value": "on"})
    assert not values_match("on", {"value": "off"})


def test_a_string_answer_is_compared_case_insensitively_and_trimmed():
    assert values_match("on", " ON ")


def test_a_boolean_is_not_matched_by_a_truthy_number():
    assert values_match(True, True)
    assert not values_match(True, 2)
