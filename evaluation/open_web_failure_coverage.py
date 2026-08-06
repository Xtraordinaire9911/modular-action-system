"""Open-web failure coverage report.

This report is deliberately conservative: it separates mechanisms that exist in
the runtime from controlled/mock evidence and from real open-web evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.open_web_mock_failure_suite import build_open_web_mock_failure_suite

FAILURE_CLASSES = {
    "optimistic_ui_backend_mismatch": {
        "description": "UI claims success while backend/API state did not change.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": [
            "postcondition mismatch / false-success detection in smart-room",
            "openweb-optimistic-rollback local fixture",
        ],
    },
    "visible_but_ineffective_affordance": {
        "description": "DOM affordance is visible but execution has no expected effect.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": [
            "expected-effect verification and recovery ledger",
            "openweb-visible-ineffective-affordance local fixture",
        ],
    },
    "dom_vs_visual_disagreement": {
        "description": "DOM tree and screenshot/OCR/visual grounding disagree.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": [
            "visual/SoM contracts exist",
            "openweb-dom-visual-disagreement local fixture",
        ],
    },
    "async_stale_state": {
        "description": "Async refresh/cache causes stale observed state.",
        "coverage_level": "controlled_evidence",
        "evidence": ["stale DOM / live ambiguous weak stale profile"],
    },
    "overlay_modal_obstruction": {
        "description": "DOM affordance exists but overlay/cookie banner/loading layer blocks operation.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": [
            "overlay filtering and affordance disappearance handling",
            "openweb-overlay-obstruction local fixture",
        ],
    },
    "ab_layout_selector_drift": {
        "description": "Layout or selector drift makes old grounding unreliable.",
        "coverage_level": "controlled_evidence",
        "evidence": ["layout_shift and selector_mutation controlled faults"],
    },
    "session_auth_expiry": {
        "description": "Session or auth expiry leaves stale page content or blocks actions.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": ["openweb-session-expiry local fixture"],
    },
    "autocomplete_async_validation_mutation": {
        "description": "Autocomplete or async validation mutates submitted value.",
        "coverage_level": "controlled_mock_evidence",
        "evidence": [
            "postcondition verification can detect final value mismatch",
            "openweb-autocomplete-validation local fixture",
        ],
    },
}


def build_open_web_failure_coverage_report() -> dict[str, Any]:
    coverage_by_class = {key: dict(value) for key, value in FAILURE_CLASSES.items()}
    counts = {
        "mechanism_ready": sum(1 for row in coverage_by_class.values() if row["coverage_level"] == "mechanism_ready"),
        "controlled_evidence": sum(
            1
            for row in coverage_by_class.values()
            if row["coverage_level"] in {"controlled_evidence", "controlled_mock_evidence"}
        ),
        "real_open_web_evidence": sum(
            1 for row in coverage_by_class.values() if row["coverage_level"] == "real_open_web_evidence"
        ),
    }
    mock_cases = build_open_web_mock_failure_suite()
    return {
        "data_source": "open_web_failure_coverage",
        "summary": {
            "failure_class_count": len(coverage_by_class),
            "mechanism_ready_count": counts["mechanism_ready"],
            "controlled_evidence_count": counts["controlled_evidence"],
            "controlled_browser_fixture_case_count": len(mock_cases),
            "real_open_web_evidence_count": counts["real_open_web_evidence"],
            "open_web_mock_case_count": len(mock_cases),
        },
        "coverage_by_class": coverage_by_class,
        "mock_suite": {
            "data_source": "open_web_mock_failure_suite",
            "coverage_level": "controlled_mock_evidence",
            "real_open_web_evidence": False,
            "case_ids": [case.case_id for case in mock_cases],
        },
        "browser_fixture_suite": {
            "data_source": "open_web_playwright_fixture_suite",
            "coverage_level": "controlled_browser_fixture_evidence",
            "runtime_entrypoint": "RuntimeEpisodeRunner.run_skill_episode",
            "real_open_web_evidence": False,
            "case_ids": [case.case_id for case in mock_cases],
        },
        "recommendation": "connect_mock_cases_to_runtime_episode_runner_then_run_real_open_web_probe",
        "next_real_open_web_cases": [
            "MiniWoB++ dynamic form validation",
            "WebArena-style auth/session expiry",
            "browser probe for overlay/cookie banner obstruction",
            "DOM-vs-screenshot/OCR disagreement with real screenshot evidence",
        ],
    }


def write_open_web_failure_coverage_report(output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = build_open_web_failure_coverage_report()
    report_path = target / "open_web_failure_coverage_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"open_web_failure_coverage_report": str(report_path)}
