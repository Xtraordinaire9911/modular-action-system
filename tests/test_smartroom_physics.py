"""The room is a room, not a dictionary with an HTTP interface.

Everything here runs against the servient from ``env/docker-compose.yml`` and is
marked ``smartroom``, because none of it can be asserted without one: the claim
being checked is about behaviour over time, and a fake that returned whatever it
was told would pass every one of these while proving nothing.

What is being pinned is the distinction the whole project rests on. A setpoint is
what the device was *told*; a measurement is what the room has *reached*. Before
this existed the servient assigned the sensor equal to the command in the same
tick, so:

* the two could never legitimately disagree, and every fusion conflict had to be
  injected rather than occurring;
* there was no window in which "the write succeeded" and "the goal is met" were
  different statements, so verifying by re-observation could not fail in a way
  that trusting the executor would not have;
* the two failure modes below - the ones a purely digital environment cannot
  produce - were unrepresentable.

The time scale is deliberate and reported by ``/state``: the room runs at 30x so
a demo fits in a meeting. These tests read the scale rather than hard-coding the
seconds it implies, so re-tuning the room does not silently invalidate them.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

WOT = "http://localhost:8080"
CONTROL = "http://localhost:8081"
TIMEOUT = 3.0

pytestmark = pytest.mark.smartroom


# ── talking to the room ──────────────────────────────────────────────────────────


def _get(thing: str, prop: str):
    request = urllib.request.Request(f"{WOT}/{thing}/properties/{prop}", headers={"X-API-Key": "demo"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - local demo
        return json.loads(response.read().decode("utf-8"))


def _put(thing: str, prop: str, value) -> None:
    request = urllib.request.Request(
        f"{WOT}/{thing}/properties/{prop}",
        method="PUT",
        data=json.dumps(value).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": "demo"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT):  # noqa: S310 - local demo
        pass


def _control(path: str, body: dict):
    request = urllib.request.Request(
        f"{CONTROL}{path}",
        method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - local demo
        return json.loads(response.read().decode("utf-8"))


def _state() -> dict:
    with urllib.request.urlopen(f"{CONTROL}/state", timeout=TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _await(thing: str, prop: str, wanted, timeout: float) -> tuple[bool, float]:
    """Wait for a measurement to arrive, and report how long it took."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if _get(thing, prop) == wanted:
            return True, time.monotonic() - started
        time.sleep(0.05)
    return False, time.monotonic() - started


@pytest.fixture(autouse=True)
def _fresh_room():
    """Every test starts from the same room and leaves no fault behind."""
    try:
        _control("/reset", {})
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - env guard
        pytest.skip(f"the smart room is not running: {exc}")
    yield
    for thing in ("projector", "blinds", "thermostat", "lights"):
        try:
            _control("/failure", {"thing": thing, "clear": True})
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass
    _control("/physics", {"enabled": True})
    _control("/reset", {})


# ── the room takes time ──────────────────────────────────────────────────────────


def test_the_room_reports_the_speed_it_is_running_at():
    """A room that reaches temperature in two seconds is running fast, and says so.

    Left only in the source, this would be a detail a reader had to discover; a
    demo would look like evidence that rooms respond instantly.
    """
    physics = _state()["physics"]

    assert physics["enabled"] is True
    assert physics["time_scale"] > 1, "a scaled room that claims 1x is lying about itself"
    assert physics["ramps"]["thermostat"]["commanded"] == "targetTemperature"
    assert physics["ramps"]["thermostat"]["measured"] == "currentTemperature"


def test_the_setpoint_is_immediate_and_the_room_is_not():
    """The distinction the project rests on, asserted against the real servient."""
    start = _get("thermostat", "currentTemperature")
    wanted = start + 3

    _put("thermostat", "targetTemperature", wanted)

    # The command is a fact the moment it is accepted.
    assert _get("thermostat", "targetTemperature") == wanted
    # The room is not. If this ever passes immediately, the sensor has gone back
    # to being a copy of the command and every verification below is vacuous.
    assert _get("thermostat", "currentTemperature") < wanted

    arrived, took = _await("thermostat", "currentTemperature", wanted, timeout=10)
    assert arrived, f"the room never reached {wanted}"
    assert took > 0.2, "arriving this fast means nothing was being modelled"


def test_a_lamp_warms_before_it_is_on():
    """Power is what the switch was told; lamp is what the room can see."""
    _put("projector", "power", "on")

    observed = []
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        observed.append(_get("projector", "lamp"))
        if observed[-1] == "on":
            break
        time.sleep(0.05)

    assert "warming" in observed, f"the lamp never warmed: {observed}"
    assert observed[-1] == "on"


# ── failures a digital write cannot have ─────────────────────────────────────────


def test_a_dead_lamp_accepts_the_command_and_stays_dark():
    """Every status code is 2xx, the setpoint reads back, and the goal is unmet.

    This is the shape of failure that separates actuating something physical from
    setting a value: nothing in the transport went wrong.
    """
    _control("/failure", {"thing": "projector", "type": "lamp_failure"})

    _put("projector", "power", "on")
    time.sleep(1.5)

    assert _get("projector", "power") == "on", "the switch was thrown and reports so"
    assert _get("projector", "lamp") == "off", "and the room is still dark"


def test_a_jammed_motor_accepts_the_position_and_never_arrives():
    _control("/failure", {"thing": "blinds", "type": "motor_jam"})
    before = _get("blinds", "measuredPosition")

    _put("blinds", "position", 20)
    time.sleep(1.5)

    assert _get("blinds", "position") == 20, "the setpoint was accepted"
    assert _get("blinds", "measuredPosition") == before, "and the blind did not move"


def test_the_blind_does_arrive_when_nothing_is_wrong():
    """The counterpart to the jam: without a fault the motor completes its travel.

    Without this, a jammed motor and a broken ramp would look identical.
    """
    _put("blinds", "position", 20)

    arrived, _ = _await("blinds", "measuredPosition", 20, timeout=10)
    assert arrived, "the blind never reached the commanded position"


# ── the escape hatch ─────────────────────────────────────────────────────────────


def test_physics_can_be_switched_off_for_runs_that_need_the_old_room():
    """Evaluation campaigns predating this should not have to change to keep working.

    Off has to mean *instantaneous*, not *frozen*: a measurement that stops
    following its command would fail every device verification rather than
    restoring the previous behaviour.
    """
    _control("/physics", {"enabled": False})

    _put("thermostat", "targetTemperature", 26)
    assert _get("thermostat", "currentTemperature") == 26, "off must mean immediate, not stuck"

    _put("projector", "power", "on")
    assert _get("projector", "lamp") == "on"

    _control("/physics", {"enabled": True})
    assert _state()["physics"]["enabled"] is True
