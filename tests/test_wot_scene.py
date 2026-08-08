"""The WoT scene must reproduce the failure the review singled out.

"The API returns success, but the actual device state does not change" is the
case that distinguishes an agent which verifies from a script which assumes, and
it was the one failure mode the demo never showed.
"""

from __future__ import annotations

import pytest

from src.demos.wot_scene import (
    FakeServient,
    WotOutcome,
    load_thermostat_td,
    perceive_device,
    read_property,
    verify_device,
    write_property,
)


def _sources():
    return perceive_device(load_thermostat_td())


def _writable():
    return next(s for s in _sources() if not s.read_only)


# --- the Thing Description drives everything ----------------------------------


def test_endpoints_come_from_the_projects_real_td():
    """No hand-written device map: the endpoints are parsed from the TD."""
    sources = _sources()

    assert sources, "the thermostat TD must yield at least one property"
    assert any(s.property == "targetTemperature" for s in sources)
    assert any(s.read_only for s in sources), "a sensor property should be read-only"
    assert all(s.href.startswith("http") for s in sources), "hrefs resolve against the TD base"


def test_read_and_write_use_the_declared_endpoints():
    servient = FakeServient({"targetTemperature": 18})
    source = _writable()

    assert read_property(servient.send, source) == 18
    write_property(servient.send, source, 22)
    assert read_property(servient.send, source) == 22
    assert ("GET", "targetTemperature", None) in servient.calls


# --- the silent failure -------------------------------------------------------


def test_silent_failure_is_acknowledged_but_changes_nothing():
    """The device answers 2xx and quietly ignores the write."""
    servient = FakeServient({"targetTemperature": 18})
    servient.silent_failure = True
    source = _writable()

    write_property(servient.send, source, 22)  # no exception: the write "succeeded"

    assert servient.state["targetTemperature"] == 18, "state must be untouched"


def test_only_re_reading_detects_the_silent_failure():
    """A successful write is not evidence; verification is what catches this."""
    servient = FakeServient({"targetTemperature": 18})
    servient.silent_failure = True
    source = _writable()

    write_property(servient.send, source, 22)

    assert verify_device(servient.send, source, 22) is False


def test_verification_passes_on_a_healthy_device():
    servient = FakeServient({"targetTemperature": 18})
    source = _writable()

    write_property(servient.send, source, 22)

    assert verify_device(servient.send, source, 22) is True


def test_recovery_after_clearing_the_fault_is_observable():
    """The retry only counts once the device actually reports the new value."""
    servient = FakeServient({"targetTemperature": 18})
    servient.silent_failure = True
    source = _writable()

    write_property(servient.send, source, 22)
    assert verify_device(servient.send, source, 22) is False

    servient.silent_failure = False  # the transient fault clears
    write_property(servient.send, source, 22)
    assert verify_device(servient.send, source, 22) is True


def test_offline_device_raises_rather_than_reporting_success():
    servient = FakeServient()
    servient.offline = True
    source = next(iter(_sources()))

    with pytest.raises(ConnectionError):
        read_property(servient.send, source)


# --- the outcome record -------------------------------------------------------


def test_outcome_tracks_failures_tiers_and_the_silent_case():
    outcome = WotOutcome(goal="Set the thermostat to 22 degrees")
    outcome.add("perceive", "2 properties parsed from the TD")
    outcome.add("act", "wrote 22, device acknowledged")
    outcome.add("verify", "device still reads 18", ok=False)
    outcome.silent_failure_caught = True
    outcome.tiers_used = [1, 2]
    outcome.recovered = True

    data = outcome.to_dict()
    assert len(outcome.failures()) == 1
    assert data["silent_failure_caught"] is True
    assert data["tiers_used"] == [1, 2]
    assert data["steps"][-1]["ok"] is False
