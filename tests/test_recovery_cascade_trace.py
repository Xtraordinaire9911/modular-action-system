from src.contracts.types import ExecutionResult, RollbackSpec, SkillTuple
from src.recovery.recovery_cascade import RecoveryCascade
from src.runtime.cognitive_map import CognitiveMap


def _skill_tuple() -> SkillTuple:
    return SkillTuple(
        skill_id="set_temperature",
        description="Set room temperature",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["wot", "dom"],
        preferred_backends=["wot", "dom"],
        rollback=RollbackSpec("set_temperature", {"target": 20}),
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
        idempotent=True,
    )


def _timeout_result() -> ExecutionResult:
    return ExecutionResult(
        skill_id="set_temperature",
        backend_used="wot",
        success=False,
        latency_ms=3000,
        confidence=0.0,
        failure_reason="timeout",
    )


def test_recovery_cascade_trace_records_selected_retry_tier():
    trace = RecoveryCascade().decide_with_trace(
        _timeout_result(),
        _skill_tuple(),
        CognitiveMap(task_id="task_trace"),
        available_backends=["dom"],
        retry_count=0,
        tried_backends=["wot"],
    )

    assert trace.selected_action == "retry"
    assert trace.selected_tier == 1
    assert trace.steps[0].policy == "retry"
    assert trace.steps[0].considered is True
    assert trace.steps[0].selected is True


def test_recovery_cascade_trace_records_skipped_retry_and_selected_reroute():
    trace = RecoveryCascade().decide_with_trace(
        _timeout_result(),
        _skill_tuple(),
        CognitiveMap(task_id="task_trace"),
        available_backends=["dom"],
        retry_count=3,
        tried_backends=["wot"],
    )

    assert trace.selected_action == "reroute"
    assert trace.selected_tier == 2
    assert [step.policy for step in trace.steps[:2]] == ["retry", "reroute"]
    assert trace.steps[0].selected is False
    assert trace.steps[1].selected is True
    assert trace.steps[1].backend == "dom"
