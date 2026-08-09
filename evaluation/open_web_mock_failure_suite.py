"""Controlled mock open-web failure suite.

The suite is intentionally conservative: it creates reproducible local
WebArena-style fixtures for common open-web failure modes, but it does not claim
that these are real open-web runs.  Each case has an explicit oracle state and an
expected runtime response so the coverage report can distinguish
mechanism-ready, controlled/mock evidence, and real open-web evidence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OpenWebMockFailureCase:
    case_id: str
    failure_class: str
    html_fixture: str
    goal: str
    observable_symptom: str
    oracle_state: dict[str, Any]
    expected_effect: str
    runtime_mechanism: str
    expected_runtime_response: str
    episode_id: str
    seed: int
    coverage_level: str = "controlled_mock_evidence"
    real_open_web: bool = False
    environment_surface: str = "local_mock_web_fixture"


def build_open_web_mock_failure_suite(seed_start: int = 8000) -> list[OpenWebMockFailureCase]:
    """Return oracle-labeled mock cases for open-web-style failures."""

    specs = [
        {
            "case_id": "openweb-overlay-obstruction",
            "failure_class": "overlay_modal_obstruction",
            "html_fixture": "failure_overlay_obstruction.html",
            "goal": "Accept the primary action while a cookie modal blocks the target button.",
            "observable_symptom": "The DOM button is visible, but an overlay intercepts the click.",
            "oracle_state": {"overlay_present": True, "primary_action_completed": False},
            "expected_effect": "primary_action_completed == true",
            "runtime_mechanism": "postcondition verification plus affordance/actionability re-observation",
            "expected_runtime_response": "detect missing expected effect and trigger recovery or escalation",
        },
        {
            "case_id": "openweb-session-expiry",
            "failure_class": "session_auth_expiry",
            "html_fixture": "failure_session_expiry.html",
            "goal": "Submit a protected profile update after the session has expired.",
            "observable_symptom": "The old form remains in the DOM, but submission is redirected to login.",
            "oracle_state": {"session_valid": False, "profile_update_persisted": False},
            "expected_effect": "profile_update_persisted == true",
            "runtime_mechanism": "fresh verification against independent oracle state",
            "expected_runtime_response": "classify auth/session failure instead of treating DOM submit success as task success",
        },
        {
            "case_id": "openweb-autocomplete-validation",
            "failure_class": "autocomplete_async_validation_mutation",
            "html_fixture": "failure_autocomplete_validation.html",
            "goal": "Enter a requested shipping city and keep the submitted value unchanged.",
            "observable_symptom": "Async validation normalizes the typed value after the agent enters it.",
            "oracle_state": {"requested_city": "New York", "submitted_city": "New York, NY"},
            "expected_effect": "submitted_city == requested_city",
            "runtime_mechanism": "expected-effect verification after fresh observation",
            "expected_runtime_response": "detect final-value mismatch and replan or ask for clarification",
        },
        {
            "case_id": "openweb-optimistic-rollback",
            "failure_class": "optimistic_ui_backend_mismatch",
            "html_fixture": "failure_optimistic_rollback.html",
            "goal": "Place an order only if the backend confirms it.",
            "observable_symptom": "UI temporarily shows order submitted while backend confirmation is failed.",
            "oracle_state": {"ui_order_submitted": True, "backend_order_confirmed": False},
            "expected_effect": "backend_order_confirmed == true",
            "runtime_mechanism": "executor-success/postcondition-success separation",
            "expected_runtime_response": "record false success, rollback or escalate rather than mark task completed",
        },
        {
            "case_id": "openweb-dom-visual-disagreement",
            "failure_class": "dom_vs_visual_disagreement",
            "html_fixture": "failure_dom_visual_disagreement.html",
            "goal": "Choose the visually highlighted active plan.",
            "observable_symptom": "DOM marks the premium plan as selected while the visible highlight is on basic.",
            "oracle_state": {"dom_selected_plan": "premium", "visual_highlighted_plan": "basic"},
            "expected_effect": "selected_plan == visual_highlighted_plan",
            "runtime_mechanism": "multi-source fusion/active perception when DOM and visual evidence disagree",
            "expected_runtime_response": "block fast path and require active perception or human escalation",
        },
        {
            "case_id": "openweb-visible-ineffective-affordance",
            "failure_class": "visible_but_ineffective_affordance",
            "html_fixture": "failure_visible_ineffective_affordance.html",
            "goal": "Enable notification settings with the visible toggle.",
            "observable_symptom": "The toggle is clickable and visible, but its state never changes.",
            "oracle_state": {"toggle_clicked": True, "notifications_enabled": False},
            "expected_effect": "notifications_enabled == true",
            "runtime_mechanism": "postcondition failure and recovery ledger",
            "expected_runtime_response": "treat click as ineffective and attempt alternate route or escalate",
        },
    ]
    return [
        OpenWebMockFailureCase(
            **spec,
            episode_id=f"openweb-mock-{index:02d}-{seed_start + index}",
            seed=seed_start + index,
        )
        for index, spec in enumerate(specs)
    ]


def build_open_web_mock_failure_suite_report(seed_start: int = 8000) -> dict[str, Any]:
    cases = build_open_web_mock_failure_suite(seed_start=seed_start)
    failure_classes = sorted({case.failure_class for case in cases})
    case_rows = [asdict(case) for case in cases]
    return {
        "data_source": "open_web_mock_failure_suite",
        "protocol": {
            "environment_surface": "local_mock_web_fixture",
            "oracle_source": "fixture_oracle_state",
            "controlled_mock_evidence": True,
            "real_open_web_evidence": False,
            "case_count": len(cases),
            "seed_start": seed_start,
            "fixture_root": "env/mock_envs",
            "runtime_path_target": "RuntimeEpisodeRunner envelope",
            "claim_boundary": "controlled mock evidence only; not real open-web evidence",
        },
        "summary": {
            "case_count": len(cases),
            "failure_class_count": len(failure_classes),
            "failure_classes": failure_classes,
            "controlled_mock_evidence_count": len(cases),
            "real_open_web_evidence_count": sum(1 for case in cases if case.real_open_web),
            "unique_episode_ids": len({case.episode_id for case in cases}) == len(cases),
            "unique_seeds": len({case.seed for case in cases}) == len(cases),
        },
        "cases": case_rows,
        "recommendation": "connect_mock_cases_to_runtime_episode_runner_then_run_real_open_web_probe",
    }


def write_open_web_mock_failure_suite_report(
    output_dir: str | Path,
    *,
    seed_start: int = 8000,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = build_open_web_mock_failure_suite_report(seed_start=seed_start)
    plan = report["cases"]
    plan_path = target / "open_web_mock_failure_plan.json"
    report_path = target / "open_web_mock_failure_suite_report.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "open_web_mock_failure_plan": str(plan_path),
        "open_web_mock_failure_suite_report": str(report_path),
    }
