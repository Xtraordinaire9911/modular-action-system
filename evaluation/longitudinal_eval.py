"""Longitudinal metrics for adaptation proposals."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LongitudinalEvalInput:
    before_normal_success_rate: float
    after_normal_success_rate: float
    before_heldout_success_rate: float
    after_heldout_success_rate: float
    safety_regressions: int
    proposal_count: int


def compare_policy_runs(data: LongitudinalEvalInput) -> dict[str, float]:
    heldout_gain = data.after_heldout_success_rate - data.before_heldout_success_rate
    backward_retention = (
        data.after_normal_success_rate / data.before_normal_success_rate if data.before_normal_success_rate else 0.0
    )
    safety_non_regression = 1.0 if data.safety_regressions == 0 else 0.0
    improvement_efficiency = heldout_gain / data.proposal_count if data.proposal_count else 0.0
    return {
        "HeldOutGain": round(heldout_gain, 10),
        "BackwardRetention": round(backward_retention, 10),
        "SafetyNonRegression": safety_non_regression,
        "ImprovementEfficiency": round(improvement_efficiency, 10),
    }
