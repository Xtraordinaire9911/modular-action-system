"""Open-web failure coverage gap report.

This report is deliberately conservative: it separates mechanisms that exist in
the runtime from controlled/mock evidence and from real open-web evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FAILURE_CLASSES = {
    "optimistic_ui_backend_mismatch": {
        "description": "UI claims success while backend/API state did not change.",
        "coverage_level": "controlled_evidence",
        "evidence": ["postcondition mismatch / false-success detection in smart-room"],
    },
    "visible_but_ineffective_affordance": {
        "description": "DOM affordance is visible but execution has no expected effect.",
        "coverage_level": "mechanism_ready",
        "evidence": ["expected-effect verification and recovery ledger"],
    },
    "dom_vs_visual_disagreement": {
        "description": "DOM tree and screenshot/OCR/visual grounding disagree.",
        "coverage_level": "mechanism_ready",
        "evidence": ["visual/SoM contracts exist; no systematic live campaign yet"],
    },
    "async_stale_state": {
        "description": "Async refresh/cache causes stale observed state.",
        "coverage_level": "controlled_evidence",
        "evidence": ["stale DOM / live ambiguous weak stale profile"],
    },
    "overlay_modal_obstruction": {
        "description": "DOM affordance exists but overlay/cookie banner/loading layer blocks operation.",
        "coverage_level": "mechanism_ready",
        "evidence": ["overlay filtering and affordance disappearance handling"],
    },
    "ab_layout_selector_drift": {
        "description": "Layout or selector drift makes old grounding unreliable.",
        "coverage_level": "controlled_evidence",
        "evidence": ["layout_shift and selector_mutation controlled faults"],
    },
    "session_auth_expiry": {
        "description": "Session or auth expiry leaves stale page content or blocks actions.",
        "coverage_level": "mechanism_ready",
        "evidence": ["no dedicated session-expiry benchmark yet"],
    },
    "autocomplete_async_validation_mutation": {
        "description": "Autocomplete or async validation mutates submitted value.",
        "coverage_level": "mechanism_ready",
        "evidence": ["postcondition verification can detect final value mismatch"],
    },
}


def build_open_web_failure_coverage_report() -> dict[str, Any]:
    coverage_by_class = {key: dict(value) for key, value in FAILURE_CLASSES.items()}
    counts = {
        "mechanism_ready": sum(1 for row in coverage_by_class.values() if row["coverage_level"] == "mechanism_ready"),
        "controlled_evidence": sum(
            1 for row in coverage_by_class.values() if row["coverage_level"] == "controlled_evidence"
        ),
        "real_open_web_evidence": sum(
            1 for row in coverage_by_class.values() if row["coverage_level"] == "real_open_web_evidence"
        ),
    }
    return {
        "data_source": "open_web_failure_coverage",
        "summary": {
            "failure_class_count": len(coverage_by_class),
            "mechanism_ready_count": counts["mechanism_ready"],
            "controlled_evidence_count": counts["controlled_evidence"],
            "real_open_web_evidence_count": counts["real_open_web_evidence"],
        },
        "coverage_by_class": coverage_by_class,
        "recommendation": "build_mock_then_real_open_web_evidence",
        "next_mock_cases": [
            "session expiry page",
            "overlay obstruction page",
            "autocomplete async validation form",
            "DOM-vs-visual disagreement fixture",
            "optimistic UI rollback fixture",
        ],
    }


def write_open_web_failure_coverage_report(output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = build_open_web_failure_coverage_report()
    report_path = target / "open_web_failure_coverage_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"open_web_failure_coverage_report": str(report_path)}
