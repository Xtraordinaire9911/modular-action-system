"""Tests for runtime WoT Thing Directory discovery (Member B).

Covers dynamic discovery without prior device knowledge, tolerant payload
shapes, malformed-entry skipping, and directory-unavailable handling. All
offline via an injected fetch function.
"""

from __future__ import annotations

import pytest

from src.perception.thing_directory import ThingDirectoryClient, ThingDirectoryError

_TD_THERMOSTAT = {
    "@context": ["https://www.w3.org/2022/wot/td/v1.1"],
    "id": "thermostat_A",
    "title": "thermostat",
    "base": "http://localhost:8080/thermostat",
    "actions": {
        "setTargetTemperature": {
            "input": {"type": "number", "minimum": 16, "maximum": 30},
            "forms": [{"op": "invokeaction", "href": "/actions/setTargetTemperature", "htv:methodName": "POST"}],
        }
    },
}
_TD_LIGHTS = {
    "@context": ["https://www.w3.org/2022/wot/td/v1.1"],
    "id": "lights_A",
    "title": "lights",
    "base": "http://localhost:8080/lights",
    "properties": {
        "brightness": {
            "type": "integer",
            "forms": [{"op": "readproperty", "href": "/properties/brightness", "htv:methodName": "GET"}],
        }
    },
}


def _fake_directory(payload):
    def fetch(url: str):
        assert url.endswith("/things")
        return payload

    return fetch


def test_discover_tds_returns_all_registered():
    client = ThingDirectoryClient("http://dir:8082", fetch_json=_fake_directory([_TD_THERMOSTAT, _TD_LIGHTS]))
    tds = client.discover_tds()
    assert {td["title"] for td in tds} == {"thermostat", "lights"}


def test_discover_models_parses_without_prior_device_knowledge():
    # The agent learns the whole inventory from the directory, not from code.
    client = ThingDirectoryClient(fetch_json=_fake_directory([_TD_THERMOSTAT, _TD_LIGHTS]))
    models = client.discover_models()
    assert {m.thing_id for m in models} == {"thermostat_A", "lights_A"}
    thermostat = next(m for m in models if m.thing_id == "thermostat_A")
    assert thermostat.action("setTargetTemperature") is not None


def test_accepts_directory_collection_object():
    client = ThingDirectoryClient(fetch_json=_fake_directory({"things": [_TD_THERMOSTAT]}))
    assert len(client.discover_tds()) == 1


def test_malformed_entry_is_skipped_not_crashing():
    client = ThingDirectoryClient(fetch_json=_fake_directory([_TD_THERMOSTAT, {"not": "a td"}]))
    models = client.discover_models()
    assert {m.thing_id for m in models} == {"thermostat_A"}


def test_unavailable_directory_raises():
    def boom(url: str):
        raise OSError("connection refused")

    with pytest.raises(ThingDirectoryError):
        ThingDirectoryClient(fetch_json=boom).discover_tds()


def test_empty_directory_raises():
    with pytest.raises(ThingDirectoryError):
        ThingDirectoryClient(fetch_json=_fake_directory([])).discover_tds()
