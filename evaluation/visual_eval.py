"""Visual grounding evaluation (Member B — Visual Grounding Accuracy / VGA).

VGA = fraction of Set-of-Marks selections that pick the correct UI target. Each
case provides the detected regions, the ground-truth label the agent should
hit, and the mark the VAM selected; we score selection correctness and report
the latency/confidence so the demo can compare visual vs deterministic paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.perception.som_parser import SetOfMarksParser


@dataclass
class VisualCase:
    task_id: str
    regions: list[dict[str, Any]]
    ground_truth_label: str
    selected_mark_id: str
    latency_ms: float = 0.0


@dataclass
class VisualReport:
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vga(self) -> float:
        if not self.rows:
            return 0.0
        return round(sum(r["correct"] for r in self.rows) / len(self.rows), 4)

    def to_dict(self) -> dict[str, Any]:
        return {"visual_grounding_accuracy": self.vga, "rows_B3": self.rows}


def evaluate(cases: list[VisualCase]) -> VisualReport:
    parser = SetOfMarksParser()
    report = VisualReport()
    for case in cases:
        affs = parser.parse(case.regions)
        try:
            grounding = parser.select(affs, case.selected_mark_id)
            correct = grounding.label.strip().lower() == case.ground_truth_label.strip().lower()
            report.rows.append(
                {
                    "task_id": case.task_id,
                    "selected_mark_id": case.selected_mark_id,
                    "selected_label": grounding.label,
                    "correct": correct,
                    "confidence": grounding.confidence,
                    "latency_ms": case.latency_ms,
                }
            )
        except KeyError:
            report.rows.append(
                {
                    "task_id": case.task_id,
                    "selected_mark_id": case.selected_mark_id,
                    "selected_label": None,
                    "correct": False,
                    "confidence": 0.0,
                    "latency_ms": case.latency_ms,
                }
            )
    return report


def write(report: VisualReport, out_dir: str | Path = "eval_outputs/backend") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "vam_executor_report.json"
    target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return target
