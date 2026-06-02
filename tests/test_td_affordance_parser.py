"""Unit tests for src/perception/td_affordance_parser.py."""

import json
import pathlib
import pytest

from src.perception.td_affordance_parser import parse_td, parse_td_file, TDParseError


_THERMOSTAT_TD = {
    "@context": ["https://www.w3.org/2019/wot/td/v1"],
    "id": "thermostat_A",
    "title": "Smart Thermostat Room A",
    "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
    "security": "nosec_sc",
    "properties": {
        "targetTemperature": {
            "type": "number",
            "readOnly": False,
            "forms": [{"href": "http://localhost:5001/thermostat/properties/targetTemperature"}],
        },
        "currentTemperature": {
            "type": "number",
            "readOnly": True,
            "forms": [{"href": "http://localhost:5001/thermostat/properties/currentTemperature"}],
        },
    },
    "actions": {
        "setTargetTemperature": {
            "input": {"type": "number", "minimum": 16, "maximum": 30},
            "forms": [
                {
                    "href": "http://localhost:5001/thermostat/actions/setTargetTemperature",
                    "htv:methodName": "POST",
                }
            ],
        }
    },
}


def test_parse_returns_affordances():
    affs = parse_td(_THERMOSTAT_TD)
    assert len(affs) == 3


def test_property_affordances_have_correct_type():
    affs = parse_td(_THERMOSTAT_TD)
    props = [a for a in affs if a.type == "property"]
    assert len(props) == 2


def test_action_affordance_has_invoke():
    affs = parse_td(_THERMOSTAT_TD)
    actions = [a for a in affs if a.type == "action"]
    assert len(actions) == 1
    assert actions[0].action == "invoke"
    assert actions[0].label == "setTargetTemperature"


def test_href_extracted_correctly():
    affs = parse_td(_THERMOSTAT_TD)
    action_aff = next(a for a in affs if a.type == "action")
    assert "setTargetTemperature" in action_aff.locator["href"]


def test_http_method_extracted():
    affs = parse_td(_THERMOSTAT_TD)
    action_aff = next(a for a in affs if a.type == "action")
    assert action_aff.locator["method"] == "POST"


def test_security_extracted():
    affs = parse_td(_THERMOSTAT_TD)
    for aff in affs:
        assert aff.state["security"]["scheme"] == "nosec"


def test_source_is_wot():
    affs = parse_td(_THERMOSTAT_TD)
    for aff in affs:
        assert aff.source == "WOT"


def test_missing_context_raises():
    bad_td = {k: v for k, v in _THERMOSTAT_TD.items() if k != "@context"}
    with pytest.raises(TDParseError):
        parse_td(bad_td)


def test_parse_td_file_from_disk(tmp_path):
    td_path = tmp_path / "thermostat.td.json"
    td_path.write_text(json.dumps(_THERMOSTAT_TD), encoding="utf-8")
    affs = parse_td_file(td_path)
    assert len(affs) == 3


def test_input_schema_in_action_state():
    affs = parse_td(_THERMOSTAT_TD)
    action_aff = next(a for a in affs if a.type == "action")
    schema = action_aff.state["input_schema"]
    assert schema["type"] == "number"
    assert schema["minimum"] == 16
