import json

from evaluation.gate_enabled_recovery_impact import (
    build_gate_enabled_recovery_impact_report,
    write_gate_enabled_recovery_impact_report,
)


def _runtime_metrics(tsr: float = 1.0) -> dict:
    return {
        "values": {
            "TSR": tsr,
            "RecoveryTriggerRate": 0.5,
            "RecoverySuccessRate": 1.0,
            "ExpectedEffectSuccessRate": 1.0,
            "FalseSuccessDetectionRate": 1.0,
        }
    }


def test_recovery_impact_report_marks_no_regression_when_gate_metrics_match_baseline():
    report = build_gate_enabled_recovery_impact_report(_runtime_metrics(), _runtime_metrics())

    assert report["data_source"] == "gate_enabled_recovery_impact"
    assert report["no_regression"] is True
    assert report["recommendation"] == "bayesian_gate_runtime_impact_passed"


def test_recovery_impact_writer_reads_metric_artifacts(tmp_path):
    baseline = tmp_path / "baseline.json"
    gate = tmp_path / "gate.json"
    baseline.write_text(json.dumps(_runtime_metrics()), encoding="utf-8")
    gate.write_text(json.dumps(_runtime_metrics()), encoding="utf-8")

    paths = write_gate_enabled_recovery_impact_report(baseline, gate, tmp_path / "out")
    report = json.loads((tmp_path / "out" / "gate_enabled_recovery_impact_report.json").read_text())

    assert paths["gate_enabled_recovery_impact_report"].endswith("gate_enabled_recovery_impact_report.json")
    assert report["source_gate_metrics"] == str(gate)
