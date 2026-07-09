"""Gate decisions for turning failure analysis into long-term candidates."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary


@dataclass(frozen=True)
class AdaptationGateDecision:
    allowed: bool
    reason: str
    needs_human_review: bool = True
    required_checks: list[str] = field(default_factory=list)


class AdaptationGate:
    def evaluate(self, analysis: FailureAnalysis) -> AdaptationGateDecision:
        if analysis.boundary == FailureBoundary.UNSAFE_GOVERNANCE_BOUNDARY:
            return AdaptationGateDecision(
                allowed=False,
                reason="unsafe governance boundary cannot become an automatic adaptation",
                needs_human_review=True,
                required_checks=["safety_non_regression", "human_approval"],
            )
        if analysis.boundary == FailureBoundary.POLICY_LEARNING_OPPORTUNITY:
            return AdaptationGateDecision(
                allowed=True,
                reason="policy learning candidate may become a reviewable proposal",
                needs_human_review=True,
                required_checks=[
                    "normal_suite_passes",
                    "failure_suite_non_regression",
                    "safety_non_regression",
                    "conflict_halt_still_blocks_system1",
                    "human_approval",
                ],
            )
        if analysis.boundary in {
            FailureBoundary.SKILL_SPEC_INSUFFICIENT,
            FailureBoundary.ARCHITECTURE_GAP,
        }:
            return AdaptationGateDecision(
                allowed=True,
                reason="candidate should remain a review artifact, not an automatic policy change",
                needs_human_review=True,
                required_checks=["human_review"],
            )
        return AdaptationGateDecision(
            allowed=False,
            reason="single-episode runtime failure should be recorded but not promoted",
            needs_human_review=False,
            required_checks=[],
        )
