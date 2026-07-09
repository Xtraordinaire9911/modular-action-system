"""Tests for evaluation metric aggregation."""

from evaluation.metrics_aggregator import (
    AdaptationCase,
    ConflictCase,
    EvaluationDataset,
    PrimitiveAction,
    RecoveryCase,
    RoutingCase,
    TaskOutcome,
    VerificationCase,
    VisualGroundingCase,
    aggregate_metrics,
    metric_definitions,
    report_rows,
)


def test_aggregate_metrics_computes_core_rates():
    dataset = EvaluationDataset(
        tasks=[
            TaskOutcome("t1", final_success=True, latency_ms=100, recovery_triggered=True),
            TaskOutcome("t2", final_success=False, latency_ms=300, unsafe_executed=True),
        ],
        recovery_cases=[
            RecoveryCase("t1", "timeout", expected_tier=1, triggered_tier=1, recovery_success=True, final_success=True),
            RecoveryCase(
                "t2",
                "postcondition_failed",
                expected_tier=3,
                triggered_tier=2,
                recovery_success=False,
                final_success=False,
            ),
        ],
        routing_cases=[
            RoutingCase("t1", selected_backend="wot", expected_backend="wot"),
            RoutingCase("t2", selected_backend="dom", expected_backend="visual"),
        ],
        verification_cases=[
            VerificationCase("t1", "set_temperature", required=True, checked=True, passed=True),
            VerificationCase("t2", "set_lighting", required=True, checked=False, passed=False),
        ],
        conflict_cases=[
            ConflictCase("t1", "room_readiness", detected=True, resolved=True),
            ConflictCase("t2", "occupancy", detected=True, resolved=False),
        ],
        visual_grounding_cases=[
            VisualGroundingCase("t1", expected_mark_id="M001", selected_mark_id="M001"),
            VisualGroundingCase("t2", expected_mark_id="M002", selected_mark_id="M003"),
        ],
        primitive_actions=[
            PrimitiveAction("t1", "invoke", latency_ms=20),
            PrimitiveAction("t1", "verify", latency_ms=40),
        ],
        adaptation_cases=[
            AdaptationCase(
                task_id="t1",
                failure_classified=True,
                full_cascade_trace=True,
                recoverable=True,
                recovered=True,
                policy_proposal_created=True,
                time_to_recovery_ms=80,
                false_success_case=True,
                false_success_detected=True,
                normal_outcome_score=1.0,
                failure_outcome_score=0.8,
                before_heldout_success_rate=0.4,
                after_heldout_success_rate=0.7,
                before_normal_success_rate=1.0,
                after_normal_success_rate=0.95,
                safety_regression=False,
                path_attributed=True,
            ),
            AdaptationCase(
                task_id="t2",
                failure_classified=False,
                full_cascade_trace=False,
                recoverable=True,
                recovered=False,
                policy_proposal_created=False,
                time_to_recovery_ms=120,
                false_success_case=True,
                false_success_detected=False,
                normal_outcome_score=1.0,
                failure_outcome_score=0.4,
                before_heldout_success_rate=0.5,
                after_heldout_success_rate=0.6,
                before_normal_success_rate=1.0,
                after_normal_success_rate=1.0,
                safety_regression=False,
                path_attributed=False,
            ),
        ],
    )

    metrics = aggregate_metrics(dataset).values

    assert metrics["TSR"] == 0.5
    assert metrics["RUR"] == 0.5
    assert metrics["RecoverySuccessRate"] == 0.5
    assert metrics["RTA"] == 0.5
    assert metrics["BRA"] == 0.5
    assert metrics["MTL"] == 200
    assert metrics["ATL"] == 30
    assert metrics["UAR"] == 0.5
    assert metrics["PCR"] == 0.5
    assert metrics["PCS"] == 1.0
    assert metrics["WDSR"] == 0.5
    assert metrics["CRR"] == 0.5
    assert metrics["VGA"] == 0.5
    assert metrics["CascadeTraceCoverage"] == 0.5
    assert metrics["BoundaryClassificationRate"] == 0.5
    assert metrics["PolicyProposalRate"] == 0.5
    assert metrics["ControlRecoveryRatio"] == 0.5
    assert metrics["MeanTimeToRecovery"] == 100
    assert metrics["FalseSuccessDetectionRate"] == 0.5
    assert metrics["CounterfactualOutcomeDeviation"] == 0.4
    assert metrics["HeldOutGain"] == 0.2
    assert metrics["BackwardRetention"] == 0.975
    assert metrics["SafetyNonRegression"] == 1.0
    assert metrics["ImprovementEfficiency"] == 0.2
    assert metrics["PathAttributionCoverage"] == 0.5


def test_metric_definitions_and_rows_are_report_friendly():
    definitions = metric_definitions()
    report = aggregate_metrics(EvaluationDataset(tasks=[TaskOutcome("t1", True)]))
    rows = report_rows(report)

    assert "TSR" in definitions
    assert "CascadeTraceCoverage" in definitions
    assert any(row["metric"] == "TSR" for row in rows)
