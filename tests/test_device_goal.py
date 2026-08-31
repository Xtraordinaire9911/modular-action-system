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
    """ "percent" is kept on lighting alone, so the phrasings already in the
    script's own help text and in the rule fallback keep working."""
    goal = composite_goal_for("room_prepared")
    lights = next(p for p in goal.parts if p.goal_state == "lighting_set")
    assert lights.value_from({}) == lights.value
    assert lights.value_from({"percent": 75}) == 75


def test_resolution_reports_the_missing_part_rather_than_the_nearest_device():
    resolved = resolve_composite_goal(composite_goal_for("room_prepared"), _room(with_blinds=False), {})
    by_goal = {part.goal_state: outcome for part, outcome in resolved}
    assert by_goal["blinds_set"].reason == "thing_not_discovered"
    assert by_goal["projector_on"].thing_id == "projector"


# ── which parameter reaches which property ──────────────────────────────────────
#
# A value can be extracted from the sentence perfectly and still never be
# written, and every layer will report success: the intent line prints the number
# it understood, the write is accepted, the read-back confirms the default that
# was written instead. Nothing in the run contradicts anything else. These tests
# exist because that is a worse failure than an error.


def test_a_temperature_named_in_the_utterance_is_written_instead_of_the_default():
    """Guards the case where the model's word for a value is not the part's word.

    The model answers "temperature", the part recognised only "degrees", and the
    run wrote 21 while printing that it had understood 22.
    """
    room = FakeRoom()

    outcome = pursue_composite_goal(
        composite_goal_for("room_prepared"), _room(), {"target": "room", "temperature": 22}, room
    )

    assert room.state["thermostat.targetTemperature"] == 22
    thermostat = next(p for p in outcome.parts if p.goal_state == "temperature_set")
    assert thermostat.wanted == 22 and thermostat.verified


def test_lights_and_blinds_can_be_given_two_different_percentages():
    """Both parts used to read parameters["percent"], so one sentence could carry
    only one percentage: "blinds at 50, lights at 15" was not expressible at all,
    and whichever part ran second wrote the other one's number."""
    room = FakeRoom()

    pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {"lighting": 15, "blinds": 50}, room)

    assert room.state["lights.brightness"] == 15
    assert room.state["blinds.position"] == 50


def test_no_two_parts_accept_the_same_parameter_name():
    """The parts read one shared dict, so a shared name is a value that cannot be
    said. This is the collision above, guarded at the declaration."""
    claimed_by: dict[str, str] = {}

    for part in composite_goal_for("room_prepared").parts:
        for name in part.value_parameters:
            assert name not in claimed_by, f"{name} is accepted by both {claimed_by.get(name)} and {part.goal_state}"
            claimed_by[name] = part.goal_state


def test_a_parameter_no_part_consumes_is_reported_rather_than_dropped():
    """The point of the whole change: the report must not agree with a sentence
    it did not carry out."""
    outcome = pursue_composite_goal(
        composite_goal_for("room_prepared"), _room(), {"target": "room", "volume": 60}, FakeRoom()
    )

    assert outcome.unconsumed_parameters == {"volume": 60}
    assert "named but not written: volume=60" in outcome.summary()
    assert outcome.to_dict()["unconsumed_parameters"] == {"volume": 60}
    # Still met: every property this goal is made of was written and confirmed.
    # An unwritable parameter is reported, not turned into a failed write, since
    # failing the goal over "at 3pm" would make the agent less useful rather than
    # more honest.
    assert outcome.verified


def test_two_names_for_one_property_report_the_one_that_was_not_used():
    """Only one number can be written, so the other was named and dropped.
    Saying which one was used is the difference between a report and a guess."""
    room = FakeRoom()

    outcome = pursue_composite_goal(
        composite_goal_for("room_prepared"), _room(), {"lighting": 15, "brightness": 20}, room
    )

    assert room.state["lights.brightness"] == 15
    assert outcome.unconsumed_parameters == {"brightness": 20}


def test_the_word_naming_the_room_is_not_reported_as_ignored():
    """The intent prompt requires a "target" on every goal, so counting it would
    fire the warning on every run, and a warning that always fires is one nobody
    reads."""
    outcome = pursue_composite_goal(
        composite_goal_for("room_prepared"), _room(), {"target": "the room", "room": "R1"}, FakeRoom()
    )

    assert outcome.unconsumed_parameters == {}


def test_a_parameter_with_no_value_is_not_reported_as_ignored():
    """A null in the model's JSON named nothing, so warning about it would be a
    false alarm competing with the real ones."""
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {"degrees": None}, FakeRoom())

    assert outcome.unconsumed_parameters == {}
    thermostat = next(p for p in outcome.parts if p.goal_state == "temperature_set")
    assert thermostat.wanted == 21, "no value was named, so the default stands"


def test_the_defaults_still_apply_when_the_utterance_names_nothing():
    """ "Prepare the room" on its own is a complete request, and must stay one."""
    room = FakeRoom()

    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room)

    assert outcome.verified
    assert room.state["lights.brightness"] == 30
    assert room.state["blinds.position"] == 20
    assert room.state["thermostat.targetTemperature"] == 21
    assert outcome.unconsumed_parameters == {}


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


# ── narrating each part as it settles ───────────────────────────────────────────
#
# The hook exists so a demo can highlight a property at the moment it is
# verified. Replaying a finished list beside a dashboard that already changed
# reads as a recording, which is the opposite of what the demo is for.


def test_each_part_is_announced_while_the_goal_is_still_being_pursued():
    """Announced during, not after: that is the whole point of the callback.

    Checked by writing to the room from inside the narrator. If the callbacks
    only ran once everything had settled, the room would already hold all four
    values on the first call.
    """
    room = FakeRoom()
    writes_seen_at_each_announcement = []

    def narrate(part):
        writes_seen_at_each_announcement.append(len(room.state))

    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room, on_part=narrate)

    assert outcome.verified
    # One more property present at each successive announcement.
    assert writes_seen_at_each_announcement == [1, 2, 3, 4]


def test_the_announced_parts_are_exactly_the_reported_ones_in_order():
    room = FakeRoom()
    announced = []
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room, on_part=announced.append)

    assert [p.goal_state for p in announced] == [p.goal_state for p in outcome.parts]
    assert all(a is b for a, b in zip(announced, outcome.parts, strict=True))


def test_a_part_this_room_lacks_is_announced_too():
    """A demo has to be able to say "this room has no blinds" out loud.

    Skipped parts never reach the executor, so it would have been easy to
    announce only the attempted ones and leave the narration silently short.
    """
    announced = []
    pursue_composite_goal(
        composite_goal_for("room_prepared"),
        _room(with_blinds=False),
        {},
        FakeRoom(),
        on_part=announced.append,
    )

    blinds = next(p for p in announced if p.goal_state == "blinds_set")
    assert blinds.skipped_reason == "thing_not_discovered"


def test_a_raising_narrator_cannot_fail_the_goal():
    """Presentation code sits on this path. A broken highlight is not a broken
    write, and the outcome must be identical to the one with no narrator at all."""
    room = FakeRoom()

    def explode(part):
        raise RuntimeError("the browser was closed mid-demo")

    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, room, on_part=explode)

    assert outcome.verified
    assert [p.goal_state for p in outcome.parts] == [
        p.goal_state for p in pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, FakeRoom()).parts
    ]


def test_the_default_is_no_narrator_so_nothing_else_had_to_change():
    outcome = pursue_composite_goal(composite_goal_for("room_prepared"), _room(), {}, FakeRoom())

    assert outcome.verified
