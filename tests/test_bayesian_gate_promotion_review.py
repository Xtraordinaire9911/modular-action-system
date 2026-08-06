import json

from evaluation.bayesian_gate_promotion_review import (
    build_bayesian_gate_promotion_review,
    write_bayesian_gate_promotion_review,
)


def _gate_summary() -> dict:
    return {
        "protocol": {"fusion_strategy": "bayesian_gate", "trial_count": 120, "profile_counts": {"a": 30}},
        "gate": {"metrics": {"balanced_accuracy": 1.0, "miss_rate": 0.0, "false_halt_rate": 0.0}},
        "rule_first": {"metrics": {"balanced_accuracy": 0.8333333333, "miss_rate": 0.3333333333}},
        "comparison": {"balanced_accuracy_delta": 0.166667, "recommendation": "gate_enabled_evaluation_passed"},
    }


def _stability() -> dict:
    return {
        "recommendation": "ready_for_integration_design_review",
        "aggregate": {"total_holdout_trials": 80, "min_balanced_accuracy_delta": 0.166667},
        "promotion_preconditions": {
            "at_least_two_holdouts": True,
            "positive_delta_in_all_holdouts": True,
            "miss_rate_not_regressed": True,
            "false_halt_not_regressed": True,
            "production_gate_unchanged_during_shadow": True,
            "profile_counts_complete": True,
        },
    }


def test_promotion_review_recommends_default_switch_when_all_gate_evidence_passes():
    report = build_bayesian_gate_promotion_review(_gate_summary(), _stability())

    assert report["data_source"] == "bayesian_gate_promotion_review"
    assert report["decision"] == "promote_bayesian_gate_as_default_candidate"
    assert report["default_switch_recommended"] is True
    assert report["must_remain_configurable"] is True


def test_promotion_review_writer_reads_inputs_and_writes_artifact(tmp_path):
    gate = tmp_path / "gate.json"
    stability = tmp_path / "stability.json"
    gate.write_text(json.dumps(_gate_summary()), encoding="utf-8")
    stability.write_text(json.dumps(_stability()), encoding="utf-8")

    paths = write_bayesian_gate_promotion_review(gate, stability, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "bayesian_gate_promotion_review.json").read_text())

    assert paths["bayesian_gate_promotion_review"].endswith("bayesian_gate_promotion_review.json")
    assert report["source_gate_enabled_summary"] == str(gate)
