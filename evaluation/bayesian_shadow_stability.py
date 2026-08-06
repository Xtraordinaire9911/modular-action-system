"""Stability report for Bayesian fusion shadow-mode promotion decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def build_bayesian_shadow_stability_report(holdout_reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reports = list(holdout_reports)
    rows = [_summarize_holdout(report, index) for index, report in enumerate(reports)]
    positive_delta = all(row["balanced_accuracy_delta"] > 0 for row in rows)
    miss_rate_improved = all(row["bayesian_miss_rate"] <= row["rule_first_miss_rate"] for row in rows)
    false_halt_not_regressed = all(
        row["bayesian_false_halt_rate"] <= row["rule_first_false_halt_rate"] for row in rows
    )
    production_unchanged = all(row["production_gate_changed"] is False for row in rows)
    profile_counts_complete = all(row["profile_counts_complete"] for row in rows)
    ready = all(
        [
            len(rows) >= 2,
            positive_delta,
            miss_rate_improved,
            false_halt_not_regressed,
            production_unchanged,
            profile_counts_complete,
        ]
    )
    return {
        "data_source": "bayesian_shadow_stability",
        "mode": "independent_rerun_stability_check",
        "holdout_count": len(rows),
        "production_strategy": "rule_first_locked_threshold",
        "shadow_strategy": "bayesian_feature_shadow",
        "production_gate_changed": False,
        "holdouts": rows,
        "aggregate": {
            "min_balanced_accuracy_delta": min((row["balanced_accuracy_delta"] for row in rows), default=0.0),
            "max_bayesian_false_halt_rate": max((row["bayesian_false_halt_rate"] for row in rows), default=0.0),
            "max_bayesian_miss_rate": max((row["bayesian_miss_rate"] for row in rows), default=0.0),
            "total_holdout_trials": sum(int(row["trial_count"]) for row in rows),
        },
        "promotion_preconditions": {
            "at_least_two_holdouts": len(rows) >= 2,
            "positive_delta_in_all_holdouts": positive_delta,
            "miss_rate_not_regressed": miss_rate_improved,
            "false_halt_not_regressed": false_halt_not_regressed,
            "production_gate_unchanged_during_shadow": production_unchanged,
            "profile_counts_complete": profile_counts_complete,
        },
        "recommendation": (
            "ready_for_integration_design_review" if ready else "keep_shadow_mode_and_collect_more_evidence"
        ),
        "boundary": {
            "shadow_mode_only": True,
            "does_not_replace_runtime_gate": True,
            "next_step_if_ready": "design configurable CIM/verifier integration, then rerun locked evaluation",
        },
    }


def write_bayesian_shadow_stability_report(
    holdout_report_paths: Iterable[str | Path],
    output_dir: str | Path,
) -> dict[str, str]:
    sources = [Path(path) for path in holdout_report_paths]
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    reports = [json.loads(source.read_text(encoding="utf-8")) for source in sources]
    report = build_bayesian_shadow_stability_report(reports)
    report["source_holdout_reports"] = [str(source) for source in sources]
    report_path = target / "bayesian_shadow_stability_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"bayesian_shadow_stability_report": str(report_path)}


def _summarize_holdout(report: dict[str, Any], index: int) -> dict[str, Any]:
    holdout = report.get("holdout", {})
    rule_metrics = holdout.get("rule_first", {}).get("metrics", {})
    bayesian_metrics = holdout.get("bayesian_shadow", {}).get("metrics", {})
    comparison = holdout.get("comparison", {})
    profile_counts = report.get("condition_counts", {}).get("holdout", {})
    rule_balanced = float(rule_metrics.get("balanced_accuracy", 0.0))
    bayesian_balanced = float(bayesian_metrics.get("balanced_accuracy", 0.0))
    return {
        "index": index,
        "source_live_ambiguous_summary": report.get("source_live_ambiguous_summary", ""),
        "trial_count": int(holdout.get("trial_count", 0)),
        "profile_counts": profile_counts,
        "profile_counts_complete": bool(profile_counts) and len(set(profile_counts.values())) == 1,
        "rule_first_balanced_accuracy": rule_balanced,
        "bayesian_balanced_accuracy": bayesian_balanced,
        "balanced_accuracy_delta": round(
            float(comparison.get("best_shadow_balanced_accuracy_delta", bayesian_balanced - rule_balanced)),
            6,
        ),
        "rule_first_miss_rate": float(rule_metrics.get("miss_rate", 0.0)),
        "bayesian_miss_rate": float(bayesian_metrics.get("miss_rate", 0.0)),
        "rule_first_false_halt_rate": float(rule_metrics.get("false_halt_rate", 0.0)),
        "bayesian_false_halt_rate": float(bayesian_metrics.get("false_halt_rate", 0.0)),
        "production_gate_changed": bool(
            report.get("protocol", {}).get(
                "production_gate_changed",
                holdout.get("strategy_comparison", {}).get("production_gate_changed", False),
            )
        ),
        "recommendation": comparison.get("recommendation", ""),
    }
