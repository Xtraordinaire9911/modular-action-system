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

    dataset = dataset_from_runtime_results(
        [result],
        ledger,
        expected_recovery_tiers={result.episode_id: 1},
    )
    report = aggregate_metrics(dataset, data_source="live", episode_ids=[result.episode_id])

    assert report.values["TSR"] == 1.0
    assert report.values["RecoveryTriggerRate"] == 1.0
    assert report.values["RecoverySuccessRate"] == 1.0
    assert report.values["RTA"] == 1.0
    assert report.metadata["data_source"] == "live"
    assert report.metadata["episode_ids"] == ["ep-live"]
    assert report.metadata["measurement_counts"]["primitive_actions"] == 1


def test_runtime_dataset_derives_primitive_and_recovery_rows_from_transition_ledger():
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
            affordance_key="wot:thermostat:set_temperature",
            backend="wot",
            params={"primitive_action": "invoke", "expected_effect": "thermostat.target == 22"},
            success=False,
            execution_success=True,
            postcondition_passed=False,
            latency_ms=0,
            attempt=1,
            observation_delta={},
            recovery_action="retry",
            recovery_tier=1,
            failure_reason="postcondition_failed",
        )
    )
    ledger.record(
        TransitionRecord(
            task_id="task-live",
            episode_id="ep-live",
            transition_id="ep-live:t2",
            step=2,
            state_id_before="s2",
            state_id_after="s3",
            skill_id="set_temperature",
            affordance_key="wot:thermostat:set_temperature",
            backend="wot",
            params={"primitive_action": "invoke", "expected_effect": "thermostat.target == 22"},
            success=True,
            execution_success=True,
            postcondition_passed=True,
            latency_ms=15,
            attempt=2,
            observation_delta={},
            recovery_action="retry",
            recovery_tier=1,
            recovery_of_transition_id="ep-live:t1",
        )
    )
    result = RuntimeStepResult(
        RuntimeState.COMPLETED,
        None,
        recovery_tier=1,
        failure_type="postcondition_failed",
        recovery_trace=[{"policy": "retry", "selected": True}],
        episode_id="ep-live",
        attempts=2,
        recovery_attempted=True,
        recovery_succeeded=True,
        final_outcome_verified=True,
    )

    dataset = dataset_from_runtime_results([result], ledger)
    report = aggregate_metrics(dataset, data_source="live", episode_ids=[result.episode_id])

    assert [action.action for action in dataset.primitive_actions] == ["invoke", "invoke"]
    assert [case.checked for case in dataset.verification_cases] == [True, True]
    assert [case.passed for case in dataset.verification_cases] == [False, True]
    assert report.values["ExpectedEffectSuccessRate"] == 0.5
    assert report.values["RetryTransitionRate"] == 1.0
    assert report.values["FalseSuccessRate"] == 0.5
    assert report.values["ATL"] == 7.5
    assert report.metadata["measurement_counts"]["primitive_actions"] == 2
    assert report.metadata["measurement_counts"]["routing_cases"] == 0


def test_safe_action_rate_is_reported_alongside_its_inverse():
    """SAR is one of the two metrics the supervisor named, so it is reported
    under that name rather than left for a reader to derive from UAR."""
    dataset = EvaluationDataset(
        tasks=[
            TaskOutcome("t1", final_success=True),
            TaskOutcome("t2", final_success=True),
            TaskOutcome("t3", final_success=False, unsafe_executed=True),
        ]
    )
    report = aggregate_metrics(dataset)

    assert report.values["UAR"] == 1 / 3
    assert report.values["SAR"] == 2 / 3
    assert report.values["SAR"] + report.values["UAR"] == 1.0
    # Same denominator, so neither can be measured while the other is not.
    assert report.denominators["SAR"] == report.denominators["UAR"] == 3
    assert "SAR" in metric_definitions()


def test_a_rate_over_no_cases_is_not_measured_rather_than_zero():
    """A zero denominator makes 0.0 meaningless: `BRA: 0.0` over no routing
    decision reads as "the router got everything wrong", which is not what
    happened."""
    dataset = EvaluationDataset(tasks=[TaskOutcome("t1", final_success=True)])
    report = aggregate_metrics(dataset)

    assert report.denominators["BRA"] == 0
    assert "BRA" in report.not_measured()
    assert "BRA" not in report.measured_values()
    assert "BRA" in report.metadata["not_measured"]

    # TSR has a real denominator here, so it stays a published measurement.
    assert report.measured_values()["TSR"] == 1.0
    assert "TSR" not in report.not_measured()


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


def test_runtime_recovery_tier_accuracy_is_omitted_without_an_independent_oracle():
    result = RuntimeStepResult(
        RuntimeState.COMPLETED,
        None,
        recovery_tier=4,
        failure_type="timeout",
        episode_id="ep-no-oracle",
        recovery_attempted=True,
        recovery_succeeded=True,
        final_outcome_verified=True,
    )

    report = aggregate_metrics(dataset_from_runtime_results([result], TransitionLedger()))

    assert "RTA" not in report.values


def test_runtime_recovery_tier_accuracy_uses_oracle_not_selected_tier():
    result = RuntimeStepResult(
        RuntimeState.ESCALATED,
        None,
        recovery_tier=4,
        failure_type="timeout",
        episode_id="ep-wrong-tier",
        recovery_attempted=True,
        recovery_succeeded=False,
        final_outcome_verified=False,
    )

    dataset = dataset_from_runtime_results(
        [result],
        TransitionLedger(),
        expected_recovery_tiers={result.episode_id: 1},
    )

    assert aggregate_metrics(dataset).values["RTA"] == 0.0


def _wot_transition(
    episode_id: str = "ep-route",
    backend: str = "wot",
    skill_id: str = "set_temperature",
    step: int = 1,
) -> TransitionRecord:
    return TransitionRecord(
        task_id="task-route",
        episode_id=episode_id,
        transition_id=f"{episode_id}:t{step}",
        step=step,
        state_id_before="s1",
        state_id_after="s2",
        skill_id=skill_id,
        affordance_key="wot:set_temperature",
        backend=backend,
        params={},
        success=True,
        execution_success=True,
        postcondition_passed=True,
        latency_ms=10,
        attempt=1,
        observation_delta={},
    )


def _completed(episode_id: str = "ep-route") -> RuntimeStepResult:
    return RuntimeStepResult(
        RuntimeState.COMPLETED,
        None,
        episode_id=episode_id,
        final_outcome_verified=True,
    )


def test_routing_cases_absent_without_oracle_labels():
    """No label means no case: BRA must stay unmeasured, not become 1.0.

    Scoring the router against its own selection would pass by construction.
    """
    ledger = TransitionLedger()
    ledger.record(_wot_transition())

    report = aggregate_metrics(dataset_from_runtime_results([_completed()], ledger))

    assert report.denominators["BRA"] == 0
    assert "BRA" in report.not_measured()


def test_routing_accuracy_scores_selected_backend_against_oracle():
    ledger = TransitionLedger()
    ledger.record(_wot_transition())

    correct = dataset_from_runtime_results(
        [_completed()], ledger, expected_backends={"ep-route": "wot"}
    )
    wrong = dataset_from_runtime_results(
        [_completed()], ledger, expected_backends={"ep-route": "dom"}
    )

    assert aggregate_metrics(correct).values["BRA"] == 1.0
    assert aggregate_metrics(wrong).values["BRA"] == 0.0
    assert aggregate_metrics(wrong).denominators["BRA"] == 1


def test_skill_label_beats_episode_label_so_rollback_is_not_a_misroute():
    """A rollback dispatch is not a wrong routing decision.

    The rollback episode writes a WoT property and then dispatches the restore
    effector. Scoring both records against one episode-level backend calls the
    restore a mis-route and reports 0.5 for a router that made no mistake, so
    the skill's own label has to win.
    """
    ledger = TransitionLedger()
    ledger.record(_wot_transition())
    ledger.record(
        _wot_transition(backend="restore", skill_id="restore_temperature", step=2)
    )

    episode_only = dataset_from_runtime_results(
        [_completed()], ledger, expected_backends={"ep-route": "wot"}
    )
    with_skill_label = dataset_from_runtime_results(
        [_completed()],
        ledger,
        expected_backends={"ep-route": "wot", "restore_temperature": "restore"},
    )

    assert aggregate_metrics(episode_only).values["BRA"] == 0.5
    assert aggregate_metrics(with_skill_label).values["BRA"] == 1.0
    assert aggregate_metrics(with_skill_label).denominators["BRA"] == 2


def test_unlabelled_skill_contributes_no_routing_case():
    """A record the oracle says nothing about must not be scored either way."""
    ledger = TransitionLedger()
    ledger.record(_wot_transition())

    dataset = dataset_from_runtime_results(
        [_completed()], ledger, expected_backends={"some_other_skill": "dom"}
    )

    assert aggregate_metrics(dataset).denominators["BRA"] == 0


class _Conflict:
    def __init__(self, conflict_type: str, resolved: bool) -> None:
        self.conflict_type = conflict_type
        self.resolved = resolved


def test_conflict_cases_derived_without_an_oracle():
    """CRR needs no label: both terms are recorded facts."""
    dataset = dataset_from_runtime_results(
        [_completed("ep-conflict")],
        TransitionLedger(),
        conflicts_by_episode={
            "ep-conflict": [
                _Conflict("page_vs_device", resolved=True),
                _Conflict("screen_vs_page", resolved=False),
            ]
        },
    )
    report = aggregate_metrics(dataset)

    assert report.values["CRR"] == 0.5
    assert report.denominators["CRR"] == 2


def test_conflict_metrics_unmeasured_when_no_conflicts_supplied():
    report = aggregate_metrics(dataset_from_runtime_results([_completed()], TransitionLedger()))

    assert "CRR" in report.not_measured()
