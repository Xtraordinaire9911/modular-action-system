"""Deterministic credit assignment for compiled runtime experience."""

from __future__ import annotations

from typing import Literal, Sequence

from src.adaptation.failure_boundary import FailureAnalysis

CreditAssignment = Literal[
    "perception_error",
    "backend_unavailability",
    "runtime_routing_policy",
    "epistemic_arbitration",
    "recovery_policy",
    "weak_postcondition",
    "skill_contract_gap",
    "environment_fault",
    "model_or_planner_gap",
    "architecture_gap",
]


def assign_credit(
    analysis: FailureAnalysis,
    *,
    backend: str,
    unresolved_conflicts: Sequence[str] | None = None,
) -> CreditAssignment:
    """Assign the most likely responsible subsystem from structured evidence."""

    failure_type = analysis.failure_type
    if unresolved_conflicts:
        return "epistemic_arbitration"
    if failure_type == "unresolved_conflict":
        return "epistemic_arbitration"
    if failure_type in {"selector_failed", "dom_grounding_failed", "visual_grounding_failed"}:
        return "perception_error"
    if failure_type in {"timeout", "rate_limited", "backend_unavailable", "executor_exception"} and backend:
        return "backend_unavailability"
    if failure_type in {"postcondition_failed", "weak_postcondition", "false_success"}:
        return "weak_postcondition"
    if failure_type in {"unknown_skill", "missing_parameter", "schema_validation_failed"}:
        return "skill_contract_gap"
    if failure_type in {"no_backend_available", "missing_environment", "unsupported_backend"}:
        return "architecture_gap"
    if failure_type in {"reroute_failed", "rollback_failed", "retry_budget_exhausted"}:
        return "recovery_policy"
    if failure_type in {"planner_failed", "ambiguous_goal"}:
        return "model_or_planner_gap"
    return "runtime_routing_policy"
