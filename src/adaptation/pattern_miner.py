"""Mine cross-episode failure patterns into reviewable policy candidates."""

from __future__ import annotations

from dataclasses import dataclass

from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary
from src.adaptation.rule_classifier import RuleFailureClassifier
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger


@dataclass(frozen=True)
class PatternProposal:
    signature: str
    proposal_type: str
    support: int
    recovery_success_rate: float
    analysis: FailureAnalysis


class FailurePatternMiner:
    """Promote only validated repeated patterns, not repeated raw errors."""

    def __init__(
        self,
        *,
        min_support: int = 3,
        min_recovery_success_rate: float = 0.75,
        min_distinct_incidents: int = 1,
        classifier: RuleFailureClassifier | None = None,
    ) -> None:
        self._min_support = min_support
        self._min_recovery_success_rate = min_recovery_success_rate
        self._min_distinct_incidents = min_distinct_incidents
        self._classifier = classifier or RuleFailureClassifier(repetition_threshold=min_support)

    def mine(self, ledger: TraceLedger) -> list[PatternProposal]:
        proposals: list[PatternProposal] = []
        for signature, events in ledger.group_by_signature().items():
            proposal = self._mine_group(signature, events)
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    def _mine_group(self, signature: str, events: list[EpisodeFailureEvent]) -> PatternProposal | None:
        support = len({event.episode_id for event in events})
        if support < self._min_support:
            return None
        if _distinct_incidents(events) < self._min_distinct_incidents:
            return None
        if any(event.safety_regression for event in events):
            return None

        recovery_success_rate = sum(1 for event in events if event.recovery_success) / len(events)
        if recovery_success_rate < self._min_recovery_success_rate:
            return None

        first = events[0]
        analysis = self._classifier.classify_pattern(
            failure_type=first.failure_type,
            skill_id=first.skill_id,
            backend=first.backend,
            same_failure_count=support,
            recovery_success_rate=recovery_success_rate,
            context_stable=True,
            safety_regression=False,
        )
        if analysis.boundary != FailureBoundary.POLICY_LEARNING_OPPORTUNITY:
            return None
        return PatternProposal(
            signature=signature,
            proposal_type=_proposal_type(first),
            support=support,
            recovery_success_rate=recovery_success_rate,
            analysis=analysis,
        )


def _distinct_incidents(events: list[EpisodeFailureEvent]) -> int:
    incidents = {event.incident_id for event in events if event.incident_id}
    if incidents:
        return len(incidents)
    return len({event.episode_id for event in events})


def _proposal_type(event: EpisodeFailureEvent) -> str:
    if event.backend:
        return "backend_reliability_adjustment"
    return "retry_budget_adjustment"
