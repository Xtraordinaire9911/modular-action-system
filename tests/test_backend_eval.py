"""Tests for backend evaluation, WoT/visual eval, and failure injection (Member B)."""

from __future__ import annotations

from evaluation.backend_eval import BackendEvaluator, BackendTrial
from evaluation.visual_eval import VisualCase, evaluate
from evaluation.wot_eval import evaluate_corpus
from scripts.inject_failures import CATALOGUE, expected_tier, robustness_plan
from src.contracts.types import ExecutionResult


def test_backend_evaluator_success_rate_and_latency_table():
    ev = BackendEvaluator()
    ev.add(BackendTrial("t1", "wot", True, 12.0, 1.0))
    ev.add(BackendTrial("t2", "wot", False, 1500.0, 1.0, "timeout"))
    ev.add(BackendTrial("t3", "dom", True, 40.0, 0.9))
    assert ev.success_rate("wot") == 0.5
    b5 = {row["backend"]: row for row in ev.latency_table()}
    assert b5["wot"]["max_latency_ms"] == 1500.0
    assert b5["dom"]["success_rate"] == 1.0


def test_backend_evaluator_from_execution_result():
    ev = BackendEvaluator()
    ev.add_result("t1", ExecutionResult("set_temp", "wot", True, 9.0, 1.0), td_parsed=True)
    row = ev.baseline_table("wot")[0]
    assert row["success"] and row["td_parsed"] is True


def test_wot_discovery_rate_full_on_compliant_tds():
    td = {
        "id": "thermostat_A",
        "title": "thermostat",
        "base": "http://h/thermostat",
        "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
        "security": "nosec_sc",
        "properties": {
            "targetTemperature": {
                "type": "number", "readOnly": False,
                "forms": [
                    {"op": "readproperty", "href": "/p/t", "htv:methodName": "GET"},
                    {"op": "writeproperty", "href": "/p/t", "htv:methodName": "PUT"},
                ],
            },
            "currentTemperature": {"type": "number", "readOnly": True,
                                   "forms": [{"op": "readproperty", "href": "/p/c"}]},
        },
        "actions": {"setTargetTemperature": {"forms": [{"op": "invokeaction", "href": "/a/s", "htv:methodName": "POST"}]}},
    }
    # expected: 1 writable + 2 readable + 1 action = 4; discovered: 1 write aff + 1 action + 2 state sources = 4
    report = evaluate_corpus([td])
    assert report["wot_discovery_success_rate"] == 1.0
    assert report["rows_B2"][0]["security_extracted"] is True


def test_visual_grounding_accuracy():
    regions = [
        {"bbox": [410, 220, 110, 40], "label": "Book Room", "confidence": 0.93},
        {"bbox": [10, 10, 30, 30], "label": "Cancel", "confidence": 0.88},
    ]
    cases = [
        VisualCase("t1", regions, ground_truth_label="Book Room", selected_mark_id="M0"),
        VisualCase("t2", regions, ground_truth_label="Book Room", selected_mark_id="M1"),  # wrong
    ]
    report = evaluate(cases)
    assert report.vga == 0.5


def test_failure_catalogue_maps_to_recovery_tiers():
    assert expected_tier("dom_selector_mutation") == "2"
    assert expected_tier("postcondition_mismatch") == "3"
    assert expected_tier("backend_offline") == "2->4"
    plan = robustness_plan()
    assert len(plan) == len(CATALOGUE)
    assert {p["failure_type"] for p in plan} >= {"wot_timeout", "perceptual_conflict", "layout_shift"}
