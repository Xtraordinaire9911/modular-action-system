"""Deterministic failure-boundary classifier.

This module deliberately avoids LLM calls. It classifies obvious runtime and
governance failures so recovery and later adaptation can start from a stable,
testable baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary
from src.contracts.types import ExecutionResult, SkillTuple
from src.runtime.cognitive_map import CognitiveMap


@dataclass(frozen=True)
class RuleFailureClassifier:
    repetition_threshold: int = 3

    def classify_unknown_skill(self, skill_id: str) -> FailureAnalysis:
        return FailureAnalysis(
            boundary=FailureBoundary.SKILL_SPEC_INSUFFICIENT,
            failure_type="unknown_skill",
            evidence=[f"skill_id={skill_id!r} is not present in the skill library"],
            immediate_action="fail_safely_and_escalate",
            long_term_action="candidate_skill_or_spec_review",
            safe_to_auto_apply=False,
            needs_human_review=True,
        )

    def classify_no_backend(
        self,
        *,
        skill_id: str,
        allowed_backends: Sequence[str],
        available_backends: Sequence[str],
    ) -> FailureAnalysis:
        return FailureAnalysis(
            boundary=FailureBoundary.ARCHITECTURE_GAP,
            failure_type="no_backend_available",
            evidence=[
                f"skill_id={skill_id!r} allowed={list(allowed_backends)!r} available={list(available_backends)!r}"
            ],
            immediate_action="fail_safely_and_escalate",
            long_term_action="architecture_or_environment_review",
            safe_to_auto_apply=False,
            needs_human_review=True,
        )

    def classify_execution_failure(
        self,
        result: ExecutionResult,
        skill_tuple: SkillTuple,
        cognitive_map: CognitiveMap,
        *,
        same_failure_count: int = 1,
    ) -> FailureAnalysis:
        unresolved_conflicts = cognitive_map.unresolved_conflicts()
        if unresolved_conflicts and skill_tuple.safety_level == "high":
            return FailureAnalysis(
                boundary=FailureBoundary.UNSAFE_GOVERNANCE_BOUNDARY,
                failure_type="unresolved_conflict",
                evidence=[conflict.description for conflict in unresolved_conflicts],
                immediate_action="block_or_human_approval",
                long_term_action="do_not_auto_learn",
                safe_to_auto_apply=False,
                needs_human_review=True,
            )

        failure_type = _normalize_failure_type(result.failure_reason)
        evidence = [
            f"skill_id={result.skill_id!r}",
            f"backend={result.backend_used!r}",
            f"failure_reason={result.failure_reason!r}",
        ]

        if failure_type in {"timeout", "rate_limited", "executor_exception"}:
            long_term_action = (
                "aggregate_before_learning" if same_failure_count >= self.repetition_threshold else "record_trace_only"
            )
            return FailureAnalysis(
                boundary=FailureBoundary.IMMEDIATE_RUNTIME_ERROR,
                failure_type=failure_type,
                evidence=evidence + _count_evidence(same_failure_count),
                immediate_action="retry_or_reroute",
                long_term_action=long_term_action,
                safe_to_auto_apply=False,
                needs_human_review=False,
            )

        if failure_type in {"postcondition_failed", "selector_failed", "backend_unavailable"}:
            return FailureAnalysis(
                boundary=FailureBoundary.RECOVERABLE_EXECUTION_FAILURE,
                failure_type=failure_type,
                evidence=evidence,
                immediate_action="use_recovery_cascade",
                long_term_action="record_recovery_pattern",
                safe_to_auto_apply=False,
                needs_human_review=False,
            )

        return FailureAnalysis(
            boundary=FailureBoundary.RECOVERABLE_EXECUTION_FAILURE,
            failure_type=failure_type,
            evidence=evidence,
            immediate_action="use_recovery_cascade",
            long_term_action="record_trace_only",
            safe_to_auto_apply=False,
            needs_human_review=False,
        )

    def classify_pattern(
        self,
        *,
        failure_type: str,
        skill_id: str,
        backend: str,
        same_failure_count: int,
        recovery_success_rate: float,
        context_stable: bool,
        safety_regression: bool,
    ) -> FailureAnalysis:
        evidence = [
            f"skill_id={skill_id!r}",
            f"backend={backend!r}",
            f"same_failure_count={same_failure_count}",
            f"recovery_success_rate={recovery_success_rate:.2f}",
            f"context_stable={context_stable}",
            f"safety_regression={safety_regression}",
        ]
        if (
            same_failure_count >= self.repetition_threshold
            and recovery_success_rate > 0.0
            and context_stable
            and not safety_regression
        ):
            return FailureAnalysis(
                boundary=FailureBoundary.POLICY_LEARNING_OPPORTUNITY,
                failure_type=failure_type,
                evidence=evidence,
                immediate_action="keep_using_recovery_cascade",
                long_term_action="propose_policy_update",
                safe_to_auto_apply=False,
                needs_human_review=True,
            )
        return FailureAnalysis(
            boundary=FailureBoundary.IMMEDIATE_RUNTIME_ERROR,
            failure_type=failure_type,
            evidence=evidence,
            immediate_action="keep_using_recovery_cascade",
            long_term_action="collect_more_evidence",
            safe_to_auto_apply=False,
            needs_human_review=False,
        )


def _normalize_failure_type(reason: str | None) -> str:
    normalized = (reason or "execution_failed").lower()
    if "timeout" in normalized:
        return "timeout"
    if "rate" in normalized and "limit" in normalized:
        return "rate_limited"
    if normalized.startswith("executor_exception"):
        return "executor_exception"
    if "postcondition" in normalized:
        return "postcondition_failed"
    if "selector" in normalized:
        return "selector_failed"
    if "backend" in normalized and ("unavailable" in normalized or "offline" in normalized):
        return "backend_unavailable"
    return normalized.replace(":", "_")


def _count_evidence(same_failure_count: int) -> list[str]:
    if same_failure_count <= 1:
        return []
    return [f"same_failure_count={same_failure_count}"]
