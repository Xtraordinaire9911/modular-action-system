from src.adaptation.failure_boundary import FailureBoundary
from src.adaptation.rule_classifier import RuleFailureClassifier
from src.contracts.types import ExecutionResult, SkillTuple
from src.runtime.cognitive_map import CognitiveMap, Conflict


def _skill_tuple(skill_id: str = "set_temperature", safety_level: str = "low") -> SkillTuple:
    return SkillTuple(
        skill_id=skill_id,
        description=skill_id,
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["wot", "dom"],
        preferred_backends=["wot"],
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level=safety_level,  # type: ignore[arg-type]
        irreversible=False,
    )


def test_classifier_marks_unknown_skill_as_skill_spec_insufficient():
    analysis = RuleFailureClassifier().classify_unknown_skill("reserve_room")

    assert analysis.boundary == FailureBoundary.SKILL_SPEC_INSUFFICIENT
    assert analysis.failure_type == "unknown_skill"
    assert analysis.immediate_action == "fail_safely_and_escalate"
    assert analysis.long_term_action == "candidate_skill_or_spec_review"
    assert not analysis.safe_to_auto_apply


def test_classifier_marks_first_timeout_as_immediate_runtime_error():
    result = ExecutionResult(
        skill_id="set_temperature",
        backend_used="wot",
        success=False,
        latency_ms=3000,
        confidence=0.0,
        failure_reason="executor_exception:TimeoutError",
    )

    analysis = RuleFailureClassifier().classify_execution_failure(
        result,
        _skill_tuple(),
        CognitiveMap(task_id="task_timeout"),
    )

    assert analysis.boundary == FailureBoundary.IMMEDIATE_RUNTIME_ERROR
    assert analysis.failure_type == "timeout"
    assert analysis.immediate_action == "retry_or_reroute"
    assert analysis.long_term_action == "record_trace_only"


def test_classifier_keeps_repeated_timeout_as_runtime_error_without_pattern_evidence():
    result = ExecutionResult(
        skill_id="set_temperature",
        backend_used="wot",
        success=False,
        latency_ms=3000,
        confidence=0.0,
        failure_reason="timeout",
    )

    analysis = RuleFailureClassifier(repetition_threshold=3).classify_execution_failure(
        result,
        _skill_tuple(),
        CognitiveMap(task_id="task_repeated_timeout"),
        same_failure_count=3,
    )

    assert analysis.boundary == FailureBoundary.IMMEDIATE_RUNTIME_ERROR
    assert analysis.long_term_action == "aggregate_before_learning"
    assert "same_failure_count=3" in analysis.evidence


def test_classifier_promotes_validated_repeated_pattern_to_policy_candidate():
    analysis = RuleFailureClassifier(repetition_threshold=3).classify_pattern(
        failure_type="timeout",
        skill_id="set_temperature",
        backend="wot",
        same_failure_count=5,
        recovery_success_rate=0.8,
        context_stable=True,
        safety_regression=False,
    )

    assert analysis.boundary == FailureBoundary.POLICY_LEARNING_OPPORTUNITY
    assert analysis.failure_type == "timeout"
    assert analysis.long_term_action == "propose_policy_update"
    assert analysis.needs_human_review


def test_classifier_marks_unresolved_high_safety_conflict_as_unsafe_boundary():
    cmap = CognitiveMap(task_id="task_unsafe_conflict")
    cmap.add_conflict(
        Conflict(
            id="door.lock",
            conflict_type="lock_mismatch",
            sources=["dom", "wot"],
            description="Door lock state conflicts.",
            severity="high",
            conflict_mass=1.0,
        )
    )
    result = ExecutionResult(
        skill_id="unlock_door",
        backend_used="wot",
        success=False,
        latency_ms=1,
        confidence=0.0,
        failure_reason="unsafe_conflict",
    )

    analysis = RuleFailureClassifier().classify_execution_failure(
        result,
        _skill_tuple("unlock_door", safety_level="high"),
        cmap,
    )

    assert analysis.boundary == FailureBoundary.UNSAFE_GOVERNANCE_BOUNDARY
    assert analysis.failure_type == "unresolved_conflict"
    assert analysis.immediate_action == "block_or_human_approval"
    assert analysis.long_term_action == "do_not_auto_learn"


def test_classifier_marks_missing_backend_as_architecture_gap():
    analysis = RuleFailureClassifier().classify_no_backend(
        skill_id="set_lighting",
        allowed_backends=["wot"],
        available_backends=[],
    )

    assert analysis.boundary == FailureBoundary.ARCHITECTURE_GAP
    assert analysis.failure_type == "no_backend_available"
    assert "allowed=['wot']" in analysis.evidence[0]
