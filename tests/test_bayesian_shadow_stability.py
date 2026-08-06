import json

from evaluation.bayesian_shadow_stability import (
    build_bayesian_shadow_stability_report,
    write_bayesian_shadow_stability_report,
)


def _holdout(delta: float, *, false_halt: float = 0.0) -> dict:
    return {
        "data_source": "live_ambiguous_fusion_locked_holdout",
        "source_live_ambiguous_summary": "summary.json",
        "condition_counts": {
            "holdout": {
                "delayed_wot_recovery": 10,
                "low_reliability_dom": 10,
                "partial_missing_wot": 10,
                "weak_stale_signal": 10,
            }
        },
        "holdout": {
            "trial_count": 40,
            "rule_first": {
                "metrics": {
                    "balanced_accuracy": 0.8333333333333333,
                    "miss_rate": 0.3333333333333333,
                    "false_halt_rate": 0.0,
                }
            },
            "bayesian_shadow": {
                "metrics": {
                    "balanced_accuracy": 0.8333333333333333 + delta,
                    "miss_rate": 0.0,
                    "false_halt_rate": false_halt,
                }
            },
            "comparison": {
                "best_shadow_strategy": "bayesian_feature_shadow",
                "best_shadow_balanced_accuracy_delta": delta,
                "recommendation": "consider_shadow_to_gate_promotion_after_independent_rerun",
            },
            "strategy_comparison": {
                "production_gate_changed": False,
                "production_strategy": "rule_first_locked_threshold",
            },
        },
    }


def test_stability_report_requires_repeated_positive_delta_and_no_false_halt_regression():
    report = build_bayesian_shadow_stability_report([_holdout(0.166667), _holdout(0.166667)])

    assert report["data_source"] == "bayesian_shadow_stability"
    assert report["holdout_count"] == 2
    assert report["promotion_preconditions"]["positive_delta_in_all_holdouts"] is True
    assert report["promotion_preconditions"]["false_halt_not_regressed"] is True
    assert report["recommendation"] == "ready_for_integration_design_review"


def test_stability_report_blocks_when_false_halt_regresses():
    report = build_bayesian_shadow_stability_report([_holdout(0.166667), _holdout(0.166667, false_halt=0.1)])

    assert report["promotion_preconditions"]["false_halt_not_regressed"] is False
    assert report["recommendation"] == "keep_shadow_mode_and_collect_more_evidence"


def test_stability_writer_reads_holdouts_and_writes_artifact(tmp_path):
    first = tmp_path / "initial.json"
    second = tmp_path / "rerun.json"
    first.write_text(json.dumps(_holdout(0.166667)), encoding="utf-8")
    second.write_text(json.dumps(_holdout(0.166667)), encoding="utf-8")

    paths = write_bayesian_shadow_stability_report([first, second], tmp_path / "out")
    report = json.loads((tmp_path / "out" / "bayesian_shadow_stability_report.json").read_text())

    assert paths["bayesian_shadow_stability_report"].endswith("bayesian_shadow_stability_report.json")
    assert report["holdout_count"] == 2
