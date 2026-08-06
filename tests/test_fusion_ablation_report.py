import json

from evaluation.fusion_ablation_report import build_fusion_ablation_report, write_fusion_ablation_report


def _holdout_report() -> dict:
    return {
        "source_live_ambiguous_summary": "summary.json",
        "holdout": {
            "trial_count": 3,
            "strategy_comparison": {
                "production_strategy": "rule_first_locked_threshold",
                "production_gate_changed": False,
                "strategies": {
                    "rule_first_locked_threshold": {"metrics": {"balanced_accuracy": 0.5, "miss_rate": 1.0}},
                    "bayesian_feature_shadow": {"metrics": {"balanced_accuracy": 1.0, "miss_rate": 0.0}},
                },
                "comparison": {
                    "best_shadow_strategy": "bayesian_feature_shadow",
                    "best_shadow_balanced_accuracy_delta": 0.5,
                    "recommendation": "consider_shadow_to_gate_promotion_after_independent_rerun",
                },
            },
        },
    }


def test_ablation_report_keeps_shadow_mode_boundary_explicit():
    report = build_fusion_ablation_report(_holdout_report())

    assert report["data_source"] == "bayesian_vs_rule_first_fusion_ablation"
    assert report["production_gate_changed"] is False
    assert report["production_strategy"] == "rule_first_locked_threshold"
    assert report["shadow_strategy"] == "bayesian_feature_shadow"
    assert report["recommendation"] == "consider_shadow_to_gate_promotion_after_independent_rerun"


def test_ablation_writer_reads_holdout_report_and_writes_artifact(tmp_path):
    source = tmp_path / "holdout.json"
    source.write_text(json.dumps(_holdout_report()), encoding="utf-8")

    paths = write_fusion_ablation_report(source, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "bayesian_vs_rule_first_ablation_report.json").read_text())

    assert paths["bayesian_vs_rule_first_ablation_report"].endswith(
        "bayesian_vs_rule_first_ablation_report.json"
    )
    assert report["source_holdout_report"] == str(source)
