"""Tests for runtime WoT TD parsing, securityDefinitions, and rate limits (Member B)."""

from __future__ import annotations

from src.perception.td_affordance_parser import TdAffordanceParser, parse_things
from src.perception.wot_security import build_auth, parse_rate_limit

_THERMOSTAT_TD = {
    "@context": ["https://www.w3.org/2022/wot/td/v1.1"],
    "id": "thermostat_A",
    "title": "Smart Thermostat Room A",
    "base": "http://localhost:8080/thermostat",
    "securityDefinitions": {"apikey_sc": {"scheme": "apikey", "in": "header", "name": "X-API-Key"}},
    "security": "apikey_sc",
    "rateLimit": "10/min",
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
    "actions": {
        "setTargetTemperature": {
            "input": {"type": "number", "minimum": 16, "maximum": 30},
            "forms": [{"op": "invokeaction", "href": "/actions/setTargetTemperature", "htv:methodName": "POST"}],
        }
    },
}


def test_action_affordance_resolves_href_and_method():
    model = TdAffordanceParser().parse(_THERMOSTAT_TD)
    aff = model.action("setTargetTemperature")
    assert aff is not None
    assert aff.source == "WOT" and aff.action == "invoke"
    assert aff.locator["href"] == "http://localhost:8080/thermostat/actions/setTargetTemperature"
    assert aff.locator["method"] == "POST"
    assert aff.state["input_schema"]["maximum"] == 30


def test_security_definitions_extracted():
    model = TdAffordanceParser().parse(_THERMOSTAT_TD)
    assert model.security is not None
    assert model.security.scheme == "apikey" and model.security.field_name == "X-API-Key"
    headers, query = build_auth(model.security, "secret-token")
    assert headers == {"X-API-Key": "secret-token"} and query == {}


def test_rate_limit_extracted_and_normalised():
    model = TdAffordanceParser().parse(_THERMOSTAT_TD)
    assert model.rate_limit is not None
    assert model.rate_limit.max_requests == 10 and model.rate_limit.window_seconds == 60
    assert model.rate_limit.min_interval_ms == 6000.0  # 60_000 / 10


def test_writable_property_yields_write_affordance_readonly_does_not():
    model = TdAffordanceParser().parse(_THERMOSTAT_TD)
    labels = {a.label for a in model.affordances}
    assert "targetTemperature" in labels  # writable → write affordance
    assert "currentTemperature" not in labels  # read-only → no write affordance


def test_state_sources_cover_all_readable_properties():
    model = TdAffordanceParser().parse(_THERMOSTAT_TD)
    props = {s.property for s in model.state_sources}
    assert props == {"targetTemperature", "currentTemperature"}
    current = next(s for s in model.state_sources if s.property == "currentTemperature")
    assert current.read_only is True


def test_malformed_td_is_skipped_not_crashing():
    good = _THERMOSTAT_TD
    bad = {"not": "a thing description"}
    models = parse_things([good, bad])
    assert len(models) == 1 and models[0].thing_id == "thermostat_A"


def test_rate_limit_parsing_variants():
    assert parse_rate_limit("5/sec").window_seconds == 1
    assert parse_rate_limit({"max": 3, "window": "minute"}).max_requests == 3
    assert parse_rate_limit({"max_requests": 2, "window_seconds": 30}).window_seconds == 30
    assert parse_rate_limit("garbage") is None
