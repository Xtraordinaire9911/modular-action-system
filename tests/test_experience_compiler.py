from evaluation.trace_logger import RuntimeTraceEvent
from src.adaptation.experience_compiler import ExperienceCompiler
from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary


def test_experience_compiler_builds_reviewable_experience_from_failure_trace():
    event = RuntimeTraceEvent(
        task_id="prepare_room_A",
        skill_id="set_temperature",
        backend="wot",
        status="failed",
        latency_ms=3000,
        attempt=1,
        recovery_tier=2,
        failure_reason="timeout",
        postcondition_passed=False,
        details={"request": {"target": 22}},
    )
    analysis = FailureAnalysis(
        boundary=FailureBoundary.IMMEDIATE_RUNTIME_ERROR,
        failure_type="timeout",
        evidence=["backend='wot'", "failure_reason='timeout'"],
        immediate_action="retry_or_reroute",
        long_term_action="record_trace_only",
        safe_to_auto_apply=False,
        needs_human_review=False,
    )

    experience = ExperienceCompiler().compile_failure(
        event,
        analysis,
        recovery_trace=[{"tier": 2, "policy": "reroute", "selected": True, "backend": "dom"}],
    )

    assert experience.task_id == "prepare_room_A"
    assert experience.failure_boundary == "immediate_runtime_error"
    assert experience.credit_assignment == "backend_unavailability"
    assert experience.immediate_action == "retry_or_reroute"
    assert experience.long_term_candidate == "record_trace_only"
    assert experience.safe_to_auto_apply is False
    assert experience.validated is False
    assert experience.recovery_trace[0]["policy"] == "reroute"
    assert "failure_reason='timeout'" in experience.evidence
