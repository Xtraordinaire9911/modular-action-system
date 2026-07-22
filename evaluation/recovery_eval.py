"""Recovery-level evaluation helpers."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.metrics_aggregator import EvaluationDataset, MetricReport, aggregate_metrics, report_rows


def write_recovery_metrics(dataset: EvaluationDataset, path: str | Path) -> MetricReport:
    report = aggregate_metrics(dataset)
    output = {
        "metadata": report.metadata,
        "values": report.values,
        "rows": report_rows(report),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    return report
