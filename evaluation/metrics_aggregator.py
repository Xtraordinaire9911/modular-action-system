"""Metric aggregation for runtime and recovery evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskOutcome:
    task_id: str
    final_success: bool
    latency_ms: float = 0.0
    recovery_triggered: bool = False
    unsafe_executed: bool = False
    unresolved_conflict: bool = False


@dataclass
class RecoveryCase:
    task_id: str
    failure_type: str
    expected_tier: int
    triggered_tier: int | None
    recovery_success: bool
    final_success: bool


@dataclass
class RoutingCase:
    task_id: str
    selected_backend: str
    expected_backend: str


@dataclass
class VerificationCase:
    task_id: str
    skill_id: str
    required: bool
    checked: bool
    passed: bool


@dataclass
class ConflictCase:
    task_id: str
    conflict_type: str
    detected: bool
    resolved: bool


@dataclass
class VisualGroundingCase:
    task_id: str
    expected_mark_id: str
    selected_mark_id: str | None


@dataclass
class PrimitiveAction:
    task_id: str
    action: str
    latency_ms: float


@dataclass
class MetricReport:
    values: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, numerator: float, denominator: float) -> None:
        self.values[name] = safe_divide(numerator, denominator)

    def add_mean(self, name: str, values: list[float]) -> None:
        self.values[name] = sum(values) / len(values) if values else 0.0


@dataclass
class EvaluationDataset:
    tasks: list[TaskOutcome] = field(default_factory=list)
    recovery_cases: list[RecoveryCase] = field(default_factory=list)
    routing_cases: list[RoutingCase] = field(default_factory=list)
    verification_cases: list[VerificationCase] = field(default_factory=list)
    conflict_cases: list[ConflictCase] = field(default_factory=list)
    visual_grounding_cases: list[VisualGroundingCase] = field(default_factory=list)
    primitive_actions: list[PrimitiveAction] = field(default_factory=list)


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def aggregate_metrics(dataset: EvaluationDataset) -> MetricReport:
    """Compute the project metrics from structured evaluation rows."""
    report = MetricReport()

    report.add(
        "TSR",
        sum(1 for task in dataset.tasks if task.final_success),
        len(dataset.tasks),
    )
    report.add(
        "RUR",
        sum(1 for task in dataset.tasks if task.recovery_triggered),
        len(dataset.tasks),
    )
    report.add(
        "RecoverySuccessRate",
        sum(1 for case in dataset.recovery_cases if case.recovery_success),
        len(dataset.recovery_cases),
    )
    report.add(
        "RTA",
        sum(1 for case in dataset.recovery_cases if case.triggered_tier == case.expected_tier),
        len(dataset.recovery_cases),
    )
    report.add(
        "BRA",
        sum(1 for case in dataset.routing_cases if case.selected_backend == case.expected_backend),
        len(dataset.routing_cases),
    )
    report.add_mean("MTL", [task.latency_ms for task in dataset.tasks])
    report.add_mean("ATL", [action.latency_ms for action in dataset.primitive_actions])
    report.add(
        "UAR",
        sum(1 for task in dataset.tasks if task.unsafe_executed),
        len(dataset.tasks),
    )
    report.add(
        "PCR",
        sum(1 for case in dataset.verification_cases if case.required and case.checked),
        sum(1 for case in dataset.verification_cases if case.required),
    )
    report.add(
        "PCS",
        sum(1 for case in dataset.verification_cases if case.checked and case.passed),
        sum(1 for case in dataset.verification_cases if case.checked),
    )
    report.add(
        "WDSR",
        sum(1 for case in dataset.conflict_cases if case.detected and case.resolved),
        len(dataset.conflict_cases),
    )
    report.add(
        "CRR",
        sum(1 for case in dataset.conflict_cases if case.resolved),
        sum(1 for case in dataset.conflict_cases if case.detected),
    )
    report.add(
        "VGA",
        sum(
            1
            for case in dataset.visual_grounding_cases
            if case.selected_mark_id == case.expected_mark_id
        ),
        len(dataset.visual_grounding_cases),
    )

    return report


def metric_definitions() -> dict[str, str]:
    """Short definitions used by reports and notebooks."""
    return {
        "TSR": "Task Success Rate = successful tasks / total tasks",
        "RUR": "Recovery Utilization Rate = tasks with recovery triggered / total tasks",
        "RecoverySuccessRate": "Recovered tasks / recovery-triggered cases",
        "RTA": "Recovery Tier Accuracy = cases with expected tier selected / failure cases",
        "BRA": "Backend Routing Accuracy = correct backend selections / routing decisions",
        "MTL": "Mean Task Latency in milliseconds",
        "ATL": "Average primitive action latency in milliseconds",
        "UAR": "Unsafe Action Rate = unsafe executed actions / attempted task actions",
        "PCR": "Postcondition Check Rate = checked required postconditions / required postconditions",
        "PCS": "Postcondition Success Rate = passed postcondition checks / checked postconditions",
        "WDSR": "World-state Disagreement Success Rate = resolved injected conflicts / injected conflicts",
        "CRR": "Conflict Resolution Rate = resolved conflicts / detected conflicts",
        "VGA": "Visual Grounding Accuracy = correct visual mark selections / visual grounding attempts",
    }


def report_rows(report: MetricReport) -> list[dict[str, Any]]:
    definitions = metric_definitions()
    return [
        {
            "metric": name,
            "value": value,
            "definition": definitions.get(name, ""),
        }
        for name, value in sorted(report.values.items())
    ]
