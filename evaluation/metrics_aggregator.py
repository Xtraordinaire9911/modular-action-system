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
    constraints_satisfied: bool | None = None
    chaos_exposed: bool = False
    attempts: int = 1


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
class OracleCase:
    task_id: str
    skill_id: str
    claimed_success: bool
    oracle_success: bool
    false_positive: bool = False
    false_negative: bool = False


@dataclass
class PrimitiveAction:
    task_id: str
    action: str
    latency_ms: float


@dataclass
class AdaptationCase:
    task_id: str
    failure_classified: bool
    full_cascade_trace: bool
    recoverable: bool
    recovered: bool
    policy_proposal_created: bool
    time_to_recovery_ms: float = 0.0
    false_success_case: bool = False
    false_success_detected: bool = False
    normal_outcome_score: float = 0.0
    failure_outcome_score: float = 0.0
    before_heldout_success_rate: float = 0.0
    after_heldout_success_rate: float = 0.0
    before_normal_success_rate: float = 0.0
    after_normal_success_rate: float = 0.0
    safety_regression: bool = False
    path_attributed: bool = False


@dataclass
class MetricReport:
    values: dict[str, float] = field(default_factory=dict)

    def add(self, name: str, numerator: float, denominator: float) -> None:
        self.values[name] = safe_divide(numerator, denominator)

    def add_mean(self, name: str, values: list[float]) -> None:
        self.values[name] = round(sum(values) / len(values), 10) if values else 0.0


@dataclass
class EvaluationDataset:
    tasks: list[TaskOutcome] = field(default_factory=list)
    recovery_cases: list[RecoveryCase] = field(default_factory=list)
    routing_cases: list[RoutingCase] = field(default_factory=list)
    verification_cases: list[VerificationCase] = field(default_factory=list)
    conflict_cases: list[ConflictCase] = field(default_factory=list)
    visual_grounding_cases: list[VisualGroundingCase] = field(default_factory=list)
    oracle_cases: list[OracleCase] = field(default_factory=list)
    primitive_actions: list[PrimitiveAction] = field(default_factory=list)
    adaptation_cases: list[AdaptationCase] = field(default_factory=list)


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
        sum(1 for case in dataset.visual_grounding_cases if case.selected_mark_id == case.expected_mark_id),
        len(dataset.visual_grounding_cases),
    )
    report.add(
        "CSR",
        sum(
            1
            for task in dataset.tasks
            if (task.constraints_satisfied if task.constraints_satisfied is not None else task.final_success)
        ),
        len(dataset.tasks),
    )
    report.add(
        "OSR",
        sum(1 for case in dataset.oracle_cases if case.oracle_success),
        len(dataset.oracle_cases),
    )
    report.add(
        "FPR",
        sum(
            1
            for case in dataset.oracle_cases
            if case.false_positive or (case.claimed_success and not case.oracle_success)
        ),
        sum(1 for case in dataset.oracle_cases if case.claimed_success),
    )
    report.add(
        "CER",
        sum(1 for task in dataset.tasks if task.chaos_exposed and task.final_success),
        sum(1 for task in dataset.tasks if task.chaos_exposed),
    )
    report.add_mean(
        "RE",
        [_recovery_efficiency(task) for task in dataset.tasks if task.recovery_triggered or task.chaos_exposed],
    )
    report.add(
        "CascadeTraceCoverage",
        sum(1 for case in dataset.adaptation_cases if case.full_cascade_trace),
        len(dataset.adaptation_cases),
    )
    report.add(
        "BoundaryClassificationRate",
        sum(1 for case in dataset.adaptation_cases if case.failure_classified),
        len(dataset.adaptation_cases),
    )
    report.add(
        "PolicyProposalRate",
        sum(1 for case in dataset.adaptation_cases if case.policy_proposal_created),
        len(dataset.adaptation_cases),
    )
    report.add(
        "ControlRecoveryRatio",
        sum(1 for case in dataset.adaptation_cases if case.recoverable and case.recovered),
        sum(1 for case in dataset.adaptation_cases if case.recoverable),
    )
    report.add_mean(
        "MeanTimeToRecovery",
        [case.time_to_recovery_ms for case in dataset.adaptation_cases if case.time_to_recovery_ms > 0],
    )
    report.add(
        "FalseSuccessDetectionRate",
        sum(1 for case in dataset.adaptation_cases if case.false_success_case and case.false_success_detected),
        sum(1 for case in dataset.adaptation_cases if case.false_success_case),
    )
    report.add_mean(
        "CounterfactualOutcomeDeviation",
        [
            abs(case.normal_outcome_score - case.failure_outcome_score)
            for case in dataset.adaptation_cases
            if case.normal_outcome_score or case.failure_outcome_score
        ],
    )
    report.add_mean(
        "HeldOutGain",
        [
            case.after_heldout_success_rate - case.before_heldout_success_rate
            for case in dataset.adaptation_cases
            if case.before_heldout_success_rate or case.after_heldout_success_rate
        ],
    )
    report.add_mean(
        "BackwardRetention",
        [
            safe_divide(case.after_normal_success_rate, case.before_normal_success_rate)
            for case in dataset.adaptation_cases
            if case.before_normal_success_rate
        ],
    )
    report.add(
        "SafetyNonRegression",
        sum(1 for case in dataset.adaptation_cases if not case.safety_regression),
        len(dataset.adaptation_cases),
    )
    proposal_count = sum(1 for case in dataset.adaptation_cases if case.policy_proposal_created)
    report.values["ImprovementEfficiency"] = safe_divide(report.values["HeldOutGain"], proposal_count)
    report.add(
        "PathAttributionCoverage",
        sum(1 for case in dataset.adaptation_cases if case.path_attributed),
        len(dataset.adaptation_cases),
    )

    return report


def _recovery_efficiency(task: TaskOutcome) -> float:
    if not task.final_success:
        return 0.0
    attempts = max(1, int(task.attempts or 1))
    return 1.0 / attempts


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
        "CSR": "Constraint Satisfaction Rate = tasks satisfying final constraints / total tasks",
        "OSR": "Oracle Success Rate = independently verified successes / oracle checks",
        "FPR": "False Positive Rate = claimed successes rejected by oracle / claimed successes",
        "CER": "Chaos Exposure Recovery Rate = chaos-exposed tasks that still succeed / chaos-exposed tasks",
        "RE": "Recovery Efficiency = mean successful recovery score, where fewer attempts score higher",
        "CascadeTraceCoverage": "Failure cases with full tier-by-tier recovery trace / total adaptation cases",
        "BoundaryClassificationRate": "Classified failures / total adaptation cases",
        "PolicyProposalRate": "Policy proposals generated / total adaptation cases",
        "ControlRecoveryRatio": "Recovered failures / recoverable adaptation cases",
        "MeanTimeToRecovery": "Mean time from failure detection to recovered/escalated state in milliseconds",
        "FalseSuccessDetectionRate": (
            "Cases where reported success was contradicted by postcondition/state checks / false-success cases"
        ),
        "CounterfactualOutcomeDeviation": "Mean distance between normal-run and failure-run final outcome scores",
        "HeldOutGain": "Mean held-out success-rate improvement after proposal",
        "BackwardRetention": "Mean normal-suite retention after proposal / before proposal",
        "SafetyNonRegression": "Adaptation cases without safety regression / total adaptation cases",
        "ImprovementEfficiency": "HeldOutGain / number of generated policy proposals",
        "PathAttributionCoverage": "Adaptation cases with subsystem credit/path attribution / total adaptation cases",
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
