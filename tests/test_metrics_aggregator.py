"""Tests for evaluation metric aggregation."""

from evaluation.metrics_aggregator import (
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
            RecoveryCase("t2", "postcondition_failed", expected_tier=3, triggered_tier=2, recovery_success=False, final_success=False),
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


def test_metric_definitions_and_rows_are_report_friendly():
    definitions = metric_definitions()
    report = aggregate_metrics(EvaluationDataset(tasks=[TaskOutcome("t1", True)]))
    rows = report_rows(report)

    assert "TSR" in definitions
    assert any(row["metric"] == "TSR" for row in rows)
