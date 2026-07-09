"""Independent outcome verification for robustness evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.contracts.types import ExecutionResult, SkillCall


@dataclass
class OracleVerdict:
    task_id: str
    skill_id: str
    claimed_success: bool
    oracle_success: bool
    false_positive: bool
    false_negative: bool
    mismatch_reason: str = ""


class OracleVerifier:
    """Compare runtime claims against a ground-truth state snapshot."""

    def verify_skill(
        self,
        *,
        task_id: str,
        skill_call: SkillCall,
        execution_result: ExecutionResult | None,
        ground_truth_state: dict[str, Any],
    ) -> OracleVerdict:
        claimed = bool(execution_result and execution_result.success)
        oracle_success, reason = _skill_satisfied(skill_call, ground_truth_state)
        return OracleVerdict(
            task_id=task_id,
            skill_id=skill_call.skill_id,
            claimed_success=claimed,
            oracle_success=oracle_success,
            false_positive=claimed and not oracle_success,
            false_negative=(not claimed) and oracle_success,
            mismatch_reason="" if oracle_success else reason,
        )

    def verify_final_state(
        self,
        *,
        task_id: str,
        expected_final_state: dict[str, Any],
        ground_truth_state: dict[str, Any],
    ) -> OracleVerdict:
        missing: list[str] = []
        for key, expected in expected_final_state.items():
            observed = ground_truth_state.get(key)
            if observed != expected:
                missing.append(f"{key}: expected {expected!r}, observed {observed!r}")
        oracle_success = not missing
        return OracleVerdict(
            task_id=task_id,
            skill_id="__final_state__",
            claimed_success=oracle_success,
            oracle_success=oracle_success,
            false_positive=False,
            false_negative=False,
            mismatch_reason="; ".join(missing),
        )


def _skill_satisfied(skill_call: SkillCall, state: dict[str, Any]) -> tuple[bool, str]:
    if skill_call.skill_id == "confirm_booking":
        ok = bool(state.get("booked") or state.get("booking_confirmed") or state.get("booking_status") == "confirmed")
        return ok, "booking is not confirmed"
    if skill_call.skill_id == "turn_on_projector":
        power = state.get("projector") or (state.get("projector_A") or {}).get("power")
        return power == "on", f"projector power is {power!r}"
    if skill_call.skill_id == "set_temperature":
        expected = skill_call.params.get("target")
        observed = state.get("target_temperature")
        if observed is None:
            observed = (state.get("thermostat_A") or {}).get("targetTemperature")
        return observed == expected, f"target_temperature is {observed!r}, expected {expected!r}"
    if skill_call.skill_id == "set_lighting":
        expected = skill_call.params.get("brightness")
        observed = state.get("light_brightness")
        if observed is None:
            observed = (state.get("lights") or {}).get("brightness")
        return observed == expected, f"light_brightness is {observed!r}, expected {expected!r}"
    if skill_call.skill_id == "verify_readiness":
        ready = state.get("readiness")
        if isinstance(ready, dict):
            ready = ready.get("ready")
        return ready is True, f"readiness is {ready!r}"
    return True, ""
