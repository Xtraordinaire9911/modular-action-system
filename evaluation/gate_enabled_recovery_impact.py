"""Compare runtime/recovery metrics under rule-first and Bayesian gate runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CORE_METRICS = (
    "TSR",
    "RecoveryTriggerRate",
    "RecoverySuccessRate",
    "ExpectedEffectSuccessRate",
    "FalseSuccessDetectionRate",
)


def build_gate_enabled_recovery_impact_report(
    baseline_metrics: dict[str, Any],
    gate_metrics: dict[str, Any],
) -> dict[str, Any]:
    baseline_values = baseline_metrics.get("values", baseline_metrics)
    gate_values = gate_metrics.get("values", gate_metrics)
    rows = {}
    regressions = []
    for metric in CORE_METRICS:
        baseline = float(baseline_values.get(metric, 0.0))
        gate = float(gate_values.get(metric, 0.0))
        delta = round(gate - baseline, 6)
        rows[metric] = {"baseline": baseline, "bayesian_gate": gate, "delta": delta}
        if (
            metric in {"TSR", "RecoverySuccessRate", "ExpectedEffectSuccessRate", "FalseSuccessDetectionRate"}
            and delta < 0
        ):
            regressions.append(metric)
    return {
        "data_source": "gate_enabled_recovery_impact",
        "source_baseline_metrics": "",
        "source_gate_metrics": "",
        "metrics": rows,
        "no_regression": not regressions,
        "regressions": regressions,
        "recommendation": (
            "bayesian_gate_runtime_impact_passed"
            if not regressions
            else "keep_rule_first_default_until_recovery_regressions_are_resolved"
        ),
    }


def write_gate_enabled_recovery_impact_report(
    baseline_metrics_path: str | Path,
    gate_metrics_path: str | Path,
    output_dir: str | Path,
) -> dict[str, str]:
    baseline_source = Path(baseline_metrics_path)
    gate_source = Path(gate_metrics_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    report = build_gate_enabled_recovery_impact_report(
        json.loads(baseline_source.read_text(encoding="utf-8")),
        json.loads(gate_source.read_text(encoding="utf-8")),
    )
    report["source_baseline_metrics"] = str(baseline_source)
    report["source_gate_metrics"] = str(gate_source)
    report_path = target / "gate_enabled_recovery_impact_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"gate_enabled_recovery_impact_report": str(report_path)}
