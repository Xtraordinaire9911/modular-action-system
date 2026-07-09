"""Tests for independent oracle verification."""

from src.contracts.types import ExecutionResult, SkillCall
from src.verification.oracle_verifier import OracleVerifier


def test_oracle_detects_false_positive_stale_wot_write():
    verifier = OracleVerifier()
    call = SkillCall("set_temperature", {"target": 22})
    result = ExecutionResult("set_temperature", "wot", True, 12.0, 1.0)

    verdict = verifier.verify_skill(
        task_id="t1",
        skill_call=call,
        execution_result=result,
        ground_truth_state={"target_temperature": 20},
    )

    assert verdict.claimed_success is True
    assert verdict.oracle_success is False
    assert verdict.false_positive is True
    assert "expected 22" in verdict.mismatch_reason


def test_oracle_accepts_matching_final_state():
    verdict = OracleVerifier().verify_final_state(
        task_id="t1",
        expected_final_state={"booked": True},
        ground_truth_state={"booked": True},
    )

    assert verdict.oracle_success is True
    assert verdict.mismatch_reason == ""
