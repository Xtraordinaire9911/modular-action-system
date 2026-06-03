"""Runtime control tests for recovery and safety decisions."""

from src.contracts.types import ExecutionResult, RollbackSpec, SkillCall, SkillTuple
from src.recovery.human_escalation import HumanEscalationPolicy
from src.recovery.retry_policy import RetryPolicy
from src.recovery.rollback_policy import RollbackPolicy
from src.recovery.reroute_policy import ReroutePolicy
from src.runtime.cognitive_map import CognitiveMap
from src.safety.unsafe_action_detector import UnsafeActionDetector


def _skill_tuple(
    allowed: list[str] | None = None,
    preferred: list[str] | None = None,
    safety_level: str = "low",
    irreversible: bool = False,
) -> SkillTuple:
    return SkillTuple(
        skill_id="set_temperature",
        description="Set room temperature",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=allowed or ["dom", "wot", "visual"],
        preferred_backends=preferred or ["wot"],
        rollback=RollbackSpec("set_temperature", {"room": "A", "target": 20}),
        failure_modes={},
        timeout_ms=3000,
        safety_level=safety_level,
        irreversible=irreversible,
    )


def test_retry_policy_retries_transient_timeout():
    result = ExecutionResult(
        skill_id="set_temperature",
        backend_used="wot",
        success=False,
        latency_ms=3000,
        confidence=0.0,
        failure_reason="timeout",
    )

    decision = RetryPolicy(max_attempts=2).decide(result, attempt=1)

    assert decision.should_retry
    assert decision.next_attempt == 2


def test_reroute_policy_selects_alternate_backend():
    decision = ReroutePolicy().decide(
        _skill_tuple(allowed=["dom", "wot", "visual"], preferred=["wot", "dom"]),
        failed_backend="wot",
        available_backends=["dom", "visual"],
        tried_backends=["wot"],
    )

    assert decision.should_reroute
    assert decision.selected_backend == "dom"


def test_rollback_policy_creates_rollback_skill_call():
    decision = RollbackPolicy().decide(_skill_tuple(), CognitiveMap(task_id="task_1"))

    assert decision.should_rollback
    assert decision.rollback_call is not None
    assert decision.rollback_call.params["target"] == 20


def test_human_escalation_for_high_risk_skill():
    decision = HumanEscalationPolicy().decide(_skill_tuple(safety_level="high"))

    assert decision.should_escalate
    assert "high" in decision.reason


def test_unsafe_action_detector_blocks_irreversible_without_confirmation():
    skill_tuple = _skill_tuple(irreversible=True)
    decision = UnsafeActionDetector().decide(
        SkillCall("set_temperature", {"room": "A", "target": 22}),
        skill_tuple,
    )

    assert not decision.allowed
    assert decision.requires_human_confirmation
