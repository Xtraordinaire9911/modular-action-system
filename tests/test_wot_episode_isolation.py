"""Device state must be restored between episodes, and gaps must be reported.

Recreating a browser context isolates the web half. The things behind the Thing
Descriptions are shared and persistent, so without this the thermostat an
episode left at 26 is the value the next episode starts from.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.effectors.wot_episode_isolation import (
    WotEpisode,
    restore_state,
    snapshot_state,
)
from src.perception.td_affordance_parser import StateAssertionSource


class FakeThings:
    """In-memory things: reads and writes hit a dict, no HTTP involved."""

    def __init__(self, values: dict[str, Any], read_only: set[str] | None = None) -> None:
        self.values = dict(values)
        self._read_only = read_only or set()
        self.unreadable: set[str] = set()
        self.unwritable: set[str] = set()
        self.writes: list[tuple[str, Any]] = []

    def state_sources(self) -> list[StateAssertionSource]:
        return [
            StateAssertionSource(
                thing_id=key.split(".")[0],
                property=key.split(".")[1],
                href=f"http://x/{key}",
                method="GET",
                read_only=key in self._read_only,
            )
            for key in self.values
        ]

    def read_state(self, source: StateAssertionSource) -> Any:
        key = f"{source.thing_id}.{source.property}"
        if key in self.unreadable:
            raise RuntimeError("offline")
        return self.values[key]

    def write_state(self, source: StateAssertionSource, value: Any) -> None:
        key = f"{source.thing_id}.{source.property}"
        if key in self.unwritable:
            raise RuntimeError("rejected")
        self.values[key] = value
        self.writes.append((key, value))


# ── snapshot ─────────────────────────────────────────────────────────────────────


def test_snapshot_reads_every_exposed_property():
    things = FakeThings({"thermostat.target": 21, "lights.on": False})
    snapshot = snapshot_state(things)

    assert snapshot.values == {"thermostat.target": 21, "lights.on": False}
    assert snapshot.is_complete


def test_read_only_properties_are_reported_not_silently_skipped():
    things = FakeThings({"thermostat.target": 21, "thermostat.current": 19}, read_only={"thermostat.current"})
    snapshot = snapshot_state(things)

    assert snapshot.read_only == ["thermostat.current"]
    assert snapshot.restorable == ["thermostat.target"]
    assert not snapshot.is_complete, "partial coverage must not look complete"


def test_unreadable_property_leaves_no_baseline_and_is_reported():
    things = FakeThings({"thermostat.target": 21, "lights.on": True})
    things.unreadable.add("lights.on")

    snapshot = snapshot_state(things)

    assert snapshot.unreadable == ["lights.on"]
    assert "lights.on" not in snapshot.values
    assert not snapshot.is_complete


# ── restore ──────────────────────────────────────────────────────────────────────


def test_restore_writes_back_only_what_the_episode_changed():
    things = FakeThings({"thermostat.target": 21, "lights.on": False})
    snapshot = snapshot_state(things)

    things.values["thermostat.target"] = 26  # the episode moved it

    report = restore_state(things, snapshot)

    assert report.restored == ["thermostat.target"]
    assert report.unchanged == ["lights.on"]
    assert things.values["thermostat.target"] == 21
    assert things.writes == [("thermostat.target", 21)], "untouched properties must not be rewritten"
    assert report.ok


def test_read_only_property_is_skipped_rather_than_attempted():
    things = FakeThings({"thermostat.current": 19}, read_only={"thermostat.current"})
    snapshot = snapshot_state(things)
    things.values["thermostat.current"] = 25

    report = restore_state(things, snapshot)

    assert report.skipped == ["thermostat.current"]
    assert things.writes == []


def test_failed_write_is_recorded_instead_of_claiming_success():
    things = FakeThings({"thermostat.target": 21})
    snapshot = snapshot_state(things)
    things.values["thermostat.target"] = 26
    things.unwritable.add("thermostat.target")

    report = restore_state(things, snapshot)

    assert "thermostat.target" in report.failed
    assert not report.ok, "a rollback that did not happen must not report ok"


def test_property_without_a_baseline_is_skipped():
    things = FakeThings({"thermostat.target": 21})
    snapshot = snapshot_state(things)
    things.values["lights.on"] = True  # appeared after the snapshot

    report = restore_state(things, snapshot)

    assert "lights.on" in report.skipped


# ── episode scope ────────────────────────────────────────────────────────────────


def test_episode_context_restores_on_exit():
    things = FakeThings({"thermostat.target": 21})

    with WotEpisode(things):
        things.values["thermostat.target"] = 26

    assert things.values["thermostat.target"] == 21


def test_episode_context_restores_even_when_the_episode_fails():
    """A failed run must not leave devices mutated for the next episode."""
    things = FakeThings({"thermostat.target": 21})

    with pytest.raises(ValueError):
        with WotEpisode(things):
            things.values["thermostat.target"] = 26
            raise ValueError("task failed")

    assert things.values["thermostat.target"] == 21


def test_episode_exposes_its_snapshot_and_report():
    things = FakeThings({"thermostat.target": 21})
    with WotEpisode(things) as episode:
        things.values["thermostat.target"] = 30

    assert episode.snapshot is not None and episode.snapshot.is_complete
    assert episode.report is not None and episode.report.restored == ["thermostat.target"]
    assert episode.report.to_dict()["ok"] is True


# ── against a real TD, through WotExecutor ───────────────────────────────────────

_THERMOSTAT_TD = {
    "@context": ["https://www.w3.org/2022/wot/td/v1.1"],
    "id": "thermostat_A",
    "title": "Smart Thermostat Room A",
    "base": "http://localhost:8080/thermostat",
    "properties": {
        "targetTemperature": {
            "type": "number",
            "readOnly": False,
            "forms": [
                {"op": "readproperty", "href": "/properties/targetTemperature", "htv:methodName": "GET"},
                {"op": "writeproperty", "href": "/properties/targetTemperature", "htv:methodName": "PUT"},
            ],
        },
        "currentTemperature": {
            "type": "number",
            "readOnly": True,
            "forms": [{"op": "readproperty", "href": "/properties/currentTemperature"}],
        },
    },
}


class FakeDevice:
    """Serves the TD's endpoints over the executor's injectable send hook."""

    def __init__(self) -> None:
        self.state = {"targetTemperature": 21, "currentTemperature": 19}
        self.requests: list[tuple[str, str, Any]] = []

    def send(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        prop = url.rsplit("/", 1)[-1]
        body = kwargs.get("json")
        self.requests.append((method, prop, body))
        if method == "GET":
            return 200, self.state[prop]
        self.state[prop] = body
        return 204, None


def _executor(device: FakeDevice):
    from src.effectors.wot_executor import WotExecutor

    return WotExecutor([_THERMOSTAT_TD], send=device.send)


def test_executor_snapshot_separates_writable_from_read_only():
    device = FakeDevice()
    snapshot = snapshot_state(_executor(device))

    assert snapshot.values["thermostat_A.targetTemperature"] == 21
    assert snapshot.read_only == ["thermostat_A.currentTemperature"]
    assert snapshot.restorable == ["thermostat_A.targetTemperature"]
    assert not snapshot.is_complete, "a read-only sensor means coverage is partial"


def test_executor_restore_uses_the_td_write_form():
    device = FakeDevice()
    executor = _executor(device)
    snapshot = snapshot_state(executor)

    device.state["targetTemperature"] = 26  # the episode moved it
    report = restore_state(executor, snapshot)

    assert report.restored == ["thermostat_A.targetTemperature"]
    assert device.state["targetTemperature"] == 21
    assert ("PUT", "targetTemperature", 21) in device.requests
    # The read-only sensor must never be written, whatever it now reads.
    assert all(prop != "currentTemperature" for method, prop, _ in device.requests if method != "GET")
