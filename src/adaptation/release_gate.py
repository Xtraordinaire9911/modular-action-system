"""Release gate for adaptation policy proposals."""

from __future__ import annotations

from dataclasses import dataclass, field

REQUIRED_CHECKS = (
    "normal_suite_passes",
    "failure_suite_non_regression",
    "safety_non_regression",
    "conflict_halt_still_blocks_system1",
    "held_out_cases_improve_or_hold",
    "cost_within_budget",
)

LOW_RISK_CHANGE_TYPES = {
    "backend_reliability_adjustment",
    "preferred_backend_order_change",
    "retry_budget_adjustment",
    "new_regression_fixture",
    "failure_profile_weight",
}

FORBIDDEN_CHANGE_TYPES = {
    "core_source_code_change",
    "model_weight_change",
    "skill_semantics_change",
    "postcondition_weakening",
    "safety_threshold_lowering",
    "human_escalation_removal",
    "conflict_halt_bypass",
}


@dataclass(frozen=True)
class ReleaseGateInput:
    proposal_id: str
    change_type: str
    checks: dict[str, bool]
    human_approved: bool = False


@dataclass(frozen=True)
class ReleaseGateResult:
    proposal_id: str
    approved: bool
    safe_to_apply: bool
    reasons: list[str] = field(default_factory=list)
    required_checks: list[str] = field(default_factory=lambda: list(REQUIRED_CHECKS))


class ReleaseGate:
    def evaluate(self, proposal: ReleaseGateInput) -> ReleaseGateResult:
        reasons: list[str] = []

        if proposal.change_type in FORBIDDEN_CHANGE_TYPES:
            reasons.append(f"forbidden change type: {proposal.change_type}")

        if proposal.change_type not in LOW_RISK_CHANGE_TYPES and proposal.change_type not in FORBIDDEN_CHANGE_TYPES:
            reasons.append(f"unsupported change type: {proposal.change_type}")

        for check in REQUIRED_CHECKS:
            if proposal.checks.get(check) is not True:
                reasons.append(f"{check} failed")

        if not proposal.human_approved:
            reasons.append("human approval is required")

        approved = not reasons
        return ReleaseGateResult(
            proposal_id=proposal.proposal_id,
            approved=approved,
            safe_to_apply=approved,
            reasons=reasons,
        )
