"""A device goal must come from what the environment published, not from code.

The failures worth guarding against are the ones that would let this quietly
become the hardcoded skill table again: resolving to a device that is not there,
writing to a sensor, or inventing a value the request never gave.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.planner.device_binding import (
    DEVICE_BINDINGS,
    DeviceResolutionError,
    ResolvedDeviceTarget,
    device_binding_for,
    resolve_device_target,
)


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
    state_sources: list[_Source]


def _thermostat(*, read_only: bool = False, base: str = "http://d:8080") -> _Model:
    return _Model(
        thing_id="thermostat",
        state_sources=[
            _Source("thermostat", "currentTemperature", f"{base}/thermostat/currentTemperature", "GET", True),
            _Source("thermostat", "targetTemperature", f"{base}/thermostat/targetTemperature", "PUT", read_only),
        ],
    )


def _lights() -> _Model:
    return _Model(
        thing_id="lights",
        state_sources=[_Source("lights", "brightness", "http://d:8080/lights/brightness", "PUT", False)],
    )


# --- resolution comes from the directory -------------------------------------------


def test_the_write_target_is_taken_from_the_discovered_thing():
    binding = device_binding_for("temperature_set")

    resolved = resolve_device_target(binding, [_thermostat(), _lights()], {"degrees": 22})

    assert isinstance(resolved, ResolvedDeviceTarget)
    assert resolved.thing_id == "thermostat" and resolved.property == "targetTemperature"
    assert resolved.href == "http://d:8080/thermostat/targetTemperature"
    assert resolved.method == "PUT"
    assert resolved.value == 22
    assert resolved.discovered_things == ["thermostat", "lights"], "the run should show what was on offer"
    assert resolved.to_dict()["resolved_from"] == "thing_directory"


def test_a_renamed_thing_still_resolves_through_its_alias():
    """The alias list maps language to TD vocabulary. It never maps a goal to a URL."""
    hvac = _Model(
        thing_id="hvac-unit-1",
        state_sources=[_Source("hvac-unit-1", "setpoint", "http://d:8080/hvac/setpoint", "PUT", False)],
    )

    resolved = resolve_device_target(device_binding_for("temperature_set"), [hvac], {"degrees": 21})

    assert isinstance(resolved, ResolvedDeviceTarget)
    assert resolved.thing_id == "hvac-unit-1" and resolved.property == "setpoint"


def test_the_read_endpoint_is_recorded_so_the_goal_can_be_verified_by_reading_back():
    resolved = resolve_device_target(device_binding_for("temperature_set"), [_thermostat()], {"degrees": 22})

    assert resolved.read_href and resolved.read_method == "GET"


# --- refusals ------------------------------------------------------------------------


def test_a_thing_that_is_not_in_the_directory_makes_the_goal_unsupported():
    """Not approximated with the nearest device that happens to exist."""
    result = resolve_device_target(device_binding_for("temperature_set"), [_lights()], {"degrees": 22})

    assert isinstance(result, DeviceResolutionError)
    assert result.reason == "thing_not_discovered"
    assert result.discovered_things == ["lights"]


def test_an_empty_directory_is_reported_rather_than_guessed_at():
    result = resolve_device_target(device_binding_for("lighting_set"), [], {"percent": 40})

    assert isinstance(result, DeviceResolutionError)
    assert result.reason == "thing_not_discovered" and result.discovered_things == []


def test_a_read_only_property_is_refused_before_anything_is_attempted():
    """Writing to a sensor is not something to discover by failing at it."""
    result = resolve_device_target(
        device_binding_for("temperature_set"), [_thermostat(read_only=True)], {"degrees": 22}
    )

    assert isinstance(result, DeviceResolutionError)
    assert result.reason == "property_read_only"
    assert "targetTemperature" in result.detail


def test_a_thing_without_the_property_is_reported_specifically():
    bare = _Model(thing_id="thermostat", state_sources=[_Source("thermostat", "humidity", "h", "GET", True)])

    result = resolve_device_target(device_binding_for("temperature_set"), [bare], {"degrees": 22})

    assert isinstance(result, DeviceResolutionError)
    assert result.reason == "property_not_discovered"


def test_a_request_with_no_value_is_refused_rather_than_defaulted():
    result = resolve_device_target(device_binding_for("temperature_set"), [_thermostat()], {})

    assert isinstance(result, DeviceResolutionError)
    assert result.reason == "no_value" and "degrees" in result.detail


# --- goals whose value is implied by the goal itself ------------------------------------


def test_a_switch_goal_carries_its_own_value():
    """ "Turn the projector on" names no number, but the value is not missing."""
    projector = _Model(
        thing_id="projector",
        state_sources=[_Source("projector", "power", "http://d:8080/projector/power", "PUT", False)],
    )

    on = resolve_device_target(device_binding_for("projector_on"), [projector], {})
    off = resolve_device_target(device_binding_for("projector_off"), [projector], {})

    assert isinstance(on, ResolvedDeviceTarget) and on.value == "on"
    assert isinstance(off, ResolvedDeviceTarget) and off.value == "off"


# --- the catalogue ----------------------------------------------------------------------


def test_every_device_binding_is_checkable_and_names_no_endpoint():
    for goal_state, binding in DEVICE_BINDINGS.items():
        assert binding.goal_state == goal_state
        assert binding.thing_aliases and binding.property_aliases
        assert binding.state_entity and binding.state_attribute, f"{goal_state} has no checkable predicate"
        assert binding.runtime_goal_state().endswith("== true")
        blob = repr(binding)
        for forbidden in ("http://", "https://", ":8080", ":8082"):
            assert forbidden not in blob, f"{goal_state} hardcodes an endpoint: {forbidden}"


def test_an_unknown_goal_state_has_no_device_binding():
    assert device_binding_for("item_in_cart") is None


# --- the measured property is a different property ---------------------------------
#
# This is the distinction the project rests on, and it was silently broken. The
# alias matcher is substring based, so "temperature" matches "targetTemperature"
# as readily as "currentTemperature", and the measured source resolved to the
# setpoint itself. Callers then read back the value they had just written, arrival
# was instant, and nothing failed.
#
# The existing fixture hid it by listing the sensor first, which is not the order
# the real servient publishes. These tests use the servient's order.


def _thermostat_setpoint_first() -> _Model:
    """The order the running servient actually publishes: setpoint, then sensor."""
    return _Model(
        thing_id="thermostat",
        state_sources=[
            _Source("thermostat", "targetTemperature", "http://d:8080/thermostat/target", "PUT", False),
            _Source("thermostat", "currentTemperature", "http://d:8080/thermostat/current", "GET", True),
        ],
    )


def test_the_measured_property_is_never_the_property_being_written():
    resolved = resolve_device_target(
        device_binding_for("temperature_set"), [_thermostat_setpoint_first()], {"degrees": 22}
    )

    assert resolved.property == "targetTemperature"
    assert resolved.measured_property == "currentTemperature", (
        "the measured reading resolved to the setpoint, so verification would confirm " "the value it just wrote"
    )


def test_the_measured_property_does_not_depend_on_the_order_the_thing_publishes():
    """Sensor first and setpoint first have to give the same answer.

    Resolution that depends on TD ordering passes on one servient and fails on
    another, which is the worst version of this bug: it looks fixed.
    """
    sensor_first = resolve_device_target(device_binding_for("temperature_set"), [_thermostat()], {"degrees": 22})
    setpoint_first = resolve_device_target(
        device_binding_for("temperature_set"), [_thermostat_setpoint_first()], {"degrees": 22}
    )

    assert sensor_first.measured_property == setpoint_first.measured_property == "currentTemperature"


def test_a_device_with_no_measured_counterpart_reports_none_rather_than_the_setpoint():
    """A dimmer really is instant. Inventing a second reading of the same property
    would report a physical delay that does not exist."""
    resolved = resolve_device_target(device_binding_for("lighting_set"), [_lights()], {"percent": 30})

    assert resolved.property == "brightness"
    assert resolved.measured_property == ""
    assert resolved.measured_source is None


def test_the_blinds_measurement_is_the_travelled_position_not_the_commanded_one():
    """The demo that answers "where is the physical part" turns on exactly this."""
    blinds = _Model(
        thing_id="blinds",
        state_sources=[
            _Source("blinds", "position", "http://d:8080/blinds/position", "PUT", False),
            _Source("blinds", "measuredPosition", "http://d:8080/blinds/measured", "GET", True),
        ],
    )

    resolved = resolve_device_target(device_binding_for("blinds_set"), [blinds], {"percent": 30})

    assert resolved.property == "position"
    assert resolved.measured_property == "measuredPosition"
