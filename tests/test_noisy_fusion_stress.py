import json

from evaluation.noisy_fusion_stress import (
    build_noisy_fusion_stress_report,
    generate_noisy_fusion_trials,
    write_noisy_fusion_stress_report,
)


def test_noisy_fusion_trials_include_ambiguous_cases_with_oracle_labels():
    trials = generate_noisy_fusion_trials(repetitions=4, seed_start=300)

    assert len(trials) == 16
    assert len({trial["episode_id"] for trial in trials}) == 16
    assert {trial["scenario"] for trial in trials} == {
        "weak_stale_signal",
        "delayed_wot_recovery",
        "low_reliability_dom",
        "partial_missing_wot",
    }
    assert all(trial["oracle_source"] == "synthetic-noisy-source-oracle" for trial in trials)
    assert any(0.0 < trial["conflict_score"] < 1.0 for trial in trials)
    assert all("source_reliability" in trial for trial in trials)


def test_noisy_stress_report_compares_rule_first_against_bayesian_features():
    trials = generate_noisy_fusion_trials(repetitions=4, seed_start=300)

    report = build_noisy_fusion_stress_report(trials, rule_threshold=1.0)

    assert report["data_source"] == "synthetic_noisy_fusion_stress"
    assert report["rule_first"]["locked_threshold"] == 1.0
    assert report["rule_first"]["metrics"]["miss_rate"] > 0.0
    assert report["bayesian"]["metrics"]["recall"] > report["rule_first"]["metrics"]["recall"]
    assert report["comparison"]["bayesian_outperforms_rule_first"] is True
    assert report["comparison"]["recommendation"] == "evaluate_on_live_ambiguous_cases_before_gate_integration"


def test_noisy_fusion_stress_writer_outputs_report(tmp_path):
    paths = write_noisy_fusion_stress_report(tmp_path, repetitions=2, seed_start=10)
    report = json.loads((tmp_path / "noisy_fusion_stress_report.json").read_text())

    assert paths["noisy_fusion_stress_report"].endswith("noisy_fusion_stress_report.json")
    assert report["protocol"]["trial_count"] == 8
