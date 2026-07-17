"""Failure boundary taxonomy for bounded trace-driven adaptation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailureBoundary(str, Enum):
    IMMEDIATE_RUNTIME_ERROR = "immediate_runtime_error"
    RECOVERABLE_EXECUTION_FAILURE = "recoverable_execution_failure"
    POLICY_LEARNING_OPPORTUNITY = "policy_learning_opportunity"
    SKILL_SPEC_INSUFFICIENT = "skill_spec_insufficient"
    ARCHITECTURE_GAP = "architecture_gap"
    UNSAFE_GOVERNANCE_BOUNDARY = "unsafe_governance_boundary"


@dataclass(frozen=True)
class FailureAnalysis:
    boundary: FailureBoundary
    failure_type: str
    evidence: list[str] = field(default_factory=list)
    immediate_action: str = ""
    long_term_action: str = ""
    confidence: float = 1.0
    safe_to_auto_apply: bool = False
    needs_human_review: bool = True
