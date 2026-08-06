import json

from evaluation.live_ambiguous_fusion_campaign import build_live_ambiguous_fusion_plan
from evaluation.live_ambiguous_fusion_holdout import (
    _strategy_row,
    build_live_ambiguous_locked_holdout_report,
    write_live_ambiguous_locked_holdout_report,
)


def _completed_trials():
    completed = []
    for trial in build_live_ambiguous_fusion_plan(repetitions=30, seed_start=900):
        score = 0.72 if trial.expected_blocking else 0.0
        completed.append(
            {
                **trial.__dict__,
                "detected_blocking": score >= 1.0,
                "conflict_score": score,
                "detection_latency_ms": 0.1,
                "reset_evidence_id": f"reset-{trial.episode_id}",
                "source_reliability": {"dom": 0.3, "wot": 0.95}
                if trial.profile == "low_reliability_dom"
                else {"dom": 0.65, "wot": 0.35}
                if trial.profile == "partial_missing_wot"
                else {"dom": 0.55, "wot": 0.85},
                "staleness_ms": 1200.0
                if trial.profile == "weak_stale_signal"
                else 900.0
                if trial.profile == "delayed_wot_recovery"
                else 100.0,
                "missing_source_probability": 0.7 if trial.profile == "partial_missing_wot" else 0.0,
            }
        )
    return completed


def test_live_ambiguous_holdout_splits_each_profile_and_keeps_rule_first_locked():
    report = build_live_ambiguous_locked_holdout_report(
        _completed_trials(),
        calibration_repetitions=20,
        holdout_repetitions=10,
        rule_threshold=1.0,
    )

    assert report["data_source"] == "live_ambiguous_fusion_locked_holdout"
    assert report["protocol"]["locked_after_calibration"] is True
    assert report["protocol"]["production_gate_changed"] is False
    assert report["protocol"]["live_fault_mapping_used_for_shadow_features"] is False
    assert report["protocol"]["shadow_feature_source"] == "recorded_runtime_trial_fields"
    assert report["calibration"]["trial_count"] == 80
    assert report["holdout"]["trial_count"] == 40
    assert set(report["condition_counts"]["holdout"].values()) == {10}
    assert report["holdout"]["rule_first"]["locked_threshold"] == 1.0
    assert report["holdout"]["bayesian_shadow"]["posterior_threshold"] == 0.5
    assert report["holdout"]["comparison"]["bayesian_outperforms_rule_first"] is True


def test_live_ambiguous_holdout_writer_reads_summary_and_writes_report(tmp_path):
    source = tmp_path / "live_ambiguous_fusion_summary.json"
    source.write_text(json.dumps({"trials": _completed_trials()}), encoding="utf-8")

    paths = write_live_ambiguous_locked_holdout_report(source, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "live_ambiguous_fusion_holdout_report.json").read_text())

    assert paths["live_ambiguous_fusion_holdout_report"].endswith("live_ambiguous_fusion_holdout_report.json")
    assert report["source_live_ambiguous_summary"] == str(source)
    assert report["holdout"]["trial_count"] == 40


def test_holdout_strategy_features_ignore_live_fault_mapping_leakage():
    row = _strategy_row(
        {
            "profile": "partial_missing_wot",
            "live_fault_mapping": {"drop_probability": 0.9, "source_reliability": {"wot": 0.1}},
            "source_reliability": {"dom": 0.8, "wot": 0.8},
            "staleness_ms": 12.0,
            "missing_source_probability": 0.05,
        }
    )

    assert row["source_reliability"] == {"dom": 0.8, "wot": 0.8}
    assert row["staleness_ms"] == 12.0
    assert row["missing_source_probability"] == 0.05
