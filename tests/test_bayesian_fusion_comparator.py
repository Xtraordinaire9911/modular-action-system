import json

from evaluation.bayesian_fusion_comparator import (
    BayesianFusionModel,
    build_bayesian_fusion_comparator_report,
    write_bayesian_fusion_comparator_report,
)


def _trial(scenario: str, expected: bool, score: float, index: int) -> dict:
    return {
        "scenario": scenario,
        "repetition": index,
        "seed": 1000 + index,
        "episode_id": f"{scenario}-{index}",
        "expected_blocking": expected,
        "detected_blocking": False,
        "conflict_score": score,
        "detection_latency_ms": 1.0,
        "source_pair": "DOM+WOT",
        "reset_evidence_id": f"reset-{scenario}-{index}",
        "oracle_source": "fault-injection-label",
        "conflict_type": "value_mismatch" if expected else "",
    }


def test_bayesian_model_converts_conflict_score_to_posterior_probability():
    model = BayesianFusionModel(score_midpoint=0.5, score_scale=8.0)

    assert model.posterior_blocking_probability(_trial("clean", False, 0.0, 0)) < 0.1
    assert model.posterior_blocking_probability(_trial("stale_temperature", True, 1.0, 1)) > 0.9


def test_comparator_report_keeps_rule_first_as_default_and_marks_no_gain_when_tied():
    holdout_report = {
        "source_campaign_summary": "campaign.json",
        "calibration": {"recommended_threshold": 1.0, "trial_count": 4},
        "holdout": {
            "locked_threshold": 1.0,
            "trial_count": 4,
            "metrics": {
                "balanced_accuracy": 1.0,
                "precision": 1.0,
                "recall": 1.0,
                "false_halt_rate": 0.0,
                "miss_rate": 0.0,
            },
            "trials": [
                _trial("clean", False, 0.0, 0),
                _trial("layout_shift", False, 0.0, 1),
                _trial("stale_temperature", True, 1.0, 2),
                _trial("wot_timeout", True, 1.0, 3),
            ],
        },
    }

    report = build_bayesian_fusion_comparator_report(holdout_report)

    assert report["mode"] == "experimental_comparator"
    assert report["production_default"] == "rule_first_locked_threshold"
    assert report["rule_first"]["locked_threshold"] == 1.0
    assert report["bayesian"]["posterior_threshold"] == 0.5
    assert report["bayesian"]["metrics"]["balanced_accuracy"] == 1.0
    assert report["comparison"]["bayesian_outperforms_rule_first"] is False
    assert report["comparison"]["recommendation"] == "keep_rule_first_default"
    assert all("posterior_blocking_probability" in row for row in report["bayesian"]["trials"])


def test_bayesian_comparator_writer_reads_holdout_report_and_writes_artifact(tmp_path):
    source = tmp_path / "fusion_holdout_report.json"
    source.write_text(
        json.dumps(
            {
                "source_campaign_summary": "campaign.json",
                "calibration": {"recommended_threshold": 1.0, "trial_count": 2},
                "holdout": {
                    "locked_threshold": 1.0,
                    "trial_count": 2,
                    "metrics": {"balanced_accuracy": 1.0},
                    "trials": [_trial("clean", False, 0.0, 0), _trial("stale_temperature", True, 1.0, 1)],
                },
            }
        ),
        encoding="utf-8",
    )

    paths = write_bayesian_fusion_comparator_report(source, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "bayesian_fusion_comparator_report.json").read_text())

    assert paths["bayesian_fusion_comparator_report"].endswith("bayesian_fusion_comparator_report.json")
    assert report["source_holdout_report"] == str(source)
