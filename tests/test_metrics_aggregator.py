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
    dataset_from_runtime_results,
    metric_definitions,
    report_rows,
)
from src.runtime.continuous_interaction_manager import RuntimeStepResult
from src.runtime.episode import TransitionLedger, TransitionRecord
from src.runtime.state_machine import RuntimeState


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
    assert metrics["RecoveryTriggerRate"] == 0.5
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


def test_runtime_metrics_are_derived_from_verified_episode_and_transition_evidence():
    ledger = TransitionLedger()
    ledger.record(
        TransitionRecord(
            task_id="task-live",
            episode_id="ep-live",
            transition_id="ep-live:t1",
            step=1,
            state_id_before="s1",
            state_id_after="s2",
            skill_id="set_temperature",
            affordance_key="wot:set_temperature",
            backend="wot",
            params={},
            success=True,
            execution_success=True,
            postcondition_passed=True,
            latency_ms=12,
            attempt=1,
            observation_delta={},
            recovery_action="retry",
            recovery_tier=1,
        )
    )
    result = RuntimeStepResult(
        RuntimeState.COMPLETED,
        None,
        recovery_tier=1,
        failure_type="timeout",
        recovery_trace=[{"policy": "retry", "selected": True}],
        episode_id="ep-live",
        attempts=2,
        recovery_attempted=True,
        recovery_succeeded=True,
        final_outcome_verified=True,
    )

    dataset = dataset_from_runtime_results([result], ledger)
    report = aggregate_metrics(dataset, data_source="live", episode_ids=[result.episode_id])

    assert report.values["TSR"] == 1.0
    assert report.values["RecoveryTriggerRate"] == 1.0
    assert report.values["RecoverySuccessRate"] == 1.0
    assert report.metadata == {"data_source": "live", "episode_ids": ["ep-live"]}


def test_verified_rollback_counts_as_recovery_but_not_task_success():
    result = RuntimeStepResult(
        RuntimeState.FAILED,
        None,
        recovery_tier=3,
        failure_type="postcondition_failed",
        recovery_trace=[{"policy": "rollback", "selected": True}],
        episode_id="ep-rollback",
        attempts=2,
        recovery_attempted=True,
        recovery_succeeded=True,
        final_outcome_verified=False,
    )

    report = aggregate_metrics(
        dataset_from_runtime_results([result], TransitionLedger()),
        data_source="live",
        episode_ids=[result.episode_id],
    )

    assert report.values["TSR"] == 0.0
    assert report.values["RecoverySuccessRate"] == 1.0
