"""Promotion review for the configurable Bayesian fusion gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_bayesian_gate_promotion_review(
    gate_enabled_summary: dict[str, Any],
    stability_report: dict[str, Any],
) -> dict[str, Any]:
    gate_metrics = gate_enabled_summary.get("gate", {}).get("metrics", {})
    rule_metrics = gate_enabled_summary.get("rule_first", {}).get("metrics", {})
    stability_preconditions = stability_report.get("promotion_preconditions", {})
    gate_passed = (
        gate_enabled_summary.get("protocol", {}).get("fusion_strategy") == "bayesian_gate"
        and float(gate_metrics.get("miss_rate", 1.0)) == 0.0
        and float(gate_metrics.get("false_halt_rate", 1.0)) == 0.0
        and float(gate_metrics.get("balanced_accuracy", 0.0)) >= float(rule_metrics.get("balanced_accuracy", 1.0))
        and gate_enabled_summary.get("comparison", {}).get("recommendation") == "gate_enabled_evaluation_passed"
    )
    stability_passed = (
        stability_report.get("recommendation") == "ready_for_integration_design_review"
        and bool(stability_preconditions)
        and all(bool(value) for value in stability_preconditions.values())
    )
    recommended = gate_passed and stability_passed
    return {
        "data_source": "bayesian_gate_promotion_review",
        "mode": "evidence_based_default_switch_review",
        "source_gate_enabled_summary": "",
        "source_stability_report": "",
        "decision": (
            "promote_bayesian_gate_as_default_candidate"
            if recommended
            else "keep_rule_first_default_and_collect_more_evidence"
        ),
        "default_switch_recommended": recommended,
        "must_remain_configurable": True,
        "default_switch_scope": "fusion gate only; fused-state selection remains support-based",
        "evidence": {
            "gate_enabled_trial_count": gate_enabled_summary.get("protocol", {}).get("trial_count", 0),
            "gate_metrics": gate_metrics,
            "rule_first_metrics": rule_metrics,
            "shadow_stability_aggregate": stability_report.get("aggregate", {}),
            "shadow_stability_preconditions": stability_preconditions,
        },
        "required_followups_after_switch": [
            "keep rule_first rollback/config option",
            "run full live runtime recovery impact suite",
            "monitor false halt and active perception trigger rate",
            "do not claim broad open-web coverage from smart-room evidence",
        ],
    }


def write_bayesian_gate_promotion_review(
    gate_enabled_summary_path: str | Path,
    stability_report_path: str | Path,
    output_dir: str | Path,
) -> dict[str, str]:
    gate_source = Path(gate_enabled_summary_path)
    stability_source = Path(stability_report_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = build_bayesian_gate_promotion_review(
        json.loads(gate_source.read_text(encoding="utf-8")),
        json.loads(stability_source.read_text(encoding="utf-8")),
    )
    report["source_gate_enabled_summary"] = str(gate_source)
    report["source_stability_report"] = str(stability_source)
    report_path = target / "bayesian_gate_promotion_review.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"bayesian_gate_promotion_review": str(report_path)}
