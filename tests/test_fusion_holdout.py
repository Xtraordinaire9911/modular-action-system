import json

from evaluation.live_fusion_campaign import RepeatedFusionTrial
from evaluation.fusion_holdout import build_locked_holdout_report
from src.pipeline import run_fusion_holdout_pipeline


def _trial(scenario: str, repetition: int, expected: bool, score: float) -> RepeatedFusionTrial:
    return RepeatedFusionTrial(
        scenario=scenario,
        repetition=repetition,
        seed=1000 + repetition,
        episode_id=f"{scenario}-{repetition}",
        expected_blocking=expected,
        detected_blocking=False,
        conflict_score=score,
        detection_latency_ms=1.0 + repetition,
        source_pair="DOM+WOT",
        reset_evidence_id=f"reset-{scenario}-{repetition}",
        oracle_source="fault-injection-label",
    )


def test_locked_holdout_report_splits_each_condition_and_evaluates_without_retuning():
    trials = [
        *[_trial("clean", i, False, 0.0) for i in range(30)],
        *[_trial("stale_temperature", i, True, 1.0) for i in range(30)],
    ]

    report = build_locked_holdout_report(
        trials,
        calibration_repetitions=20,
        holdout_repetitions=10,
        thresholds=[0.5, 1.0],
    )

    assert report["protocol"]["locked_after_calibration"] is True
    assert report["protocol"]["calibration_repetitions_per_condition"] == 20
    assert report["protocol"]["holdout_repetitions_per_condition"] == 10
    assert report["protocol"]["holdout_uses_calibration_threshold"] is True
    assert report["calibration"]["trial_count"] == 40
    assert report["holdout"]["trial_count"] == 20
    assert report["calibration"]["recommended_threshold"] == report["holdout"]["locked_threshold"]
    assert report["holdout"]["metrics"]["recall"] == 1.0
    assert report["holdout"]["metrics"]["false_halt_rate"] == 0.0
    assert report["condition_counts"]["calibration"] == {"clean": 20, "stale_temperature": 20}
    assert report["condition_counts"]["holdout"] == {"clean": 10, "stale_temperature": 10}


def test_fusion_holdout_pipeline_reads_campaign_summary_and_writes_report(tmp_path):
    campaign = {
        "trials": [
            *[json.loads(json.dumps(_trial("clean", i, False, 0.0).__dict__)) for i in range(30)],
            *[json.loads(json.dumps(_trial("stale_temperature", i, True, 1.0).__dict__)) for i in range(30)],
        ]
    }
    source = tmp_path / "campaign_summary.json"
    source.write_text(json.dumps(campaign), encoding="utf-8")

    paths = run_fusion_holdout_pipeline(source, tmp_path / "holdout", calibration_repetitions=20)
    report = json.loads((tmp_path / "holdout" / "fusion_holdout_report.json").read_text())

    assert paths["fusion_holdout_report"].endswith("fusion_holdout_report.json")
    assert report["source_campaign_summary"] == str(source)
    assert report["holdout"]["trial_count"] == 20
