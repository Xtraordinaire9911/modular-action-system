"""Tier 4 recovery: human escalation."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import SkillTuple


@dataclass
class EscalationDecision:
    should_escalate: bool
    reason: str


class HumanEscalationPolicy:
    def decide(
        self,
        skill_tuple: SkillTuple,
        automated_options_exhausted: bool = False,
        unresolved_conflict: bool = False,
    ) -> EscalationDecision:
        if skill_tuple.irreversible:
            return EscalationDecision(True, "irreversible action requires human escalation")
        if skill_tuple.safety_level == "high":
            return EscalationDecision(True, "high safety level requires human escalation")
        if unresolved_conflict:
            return EscalationDecision(True, "unresolved perceptual conflict")
        if automated_options_exhausted:
            return EscalationDecision(True, "automated recovery exhausted")
        return EscalationDecision(False, "no escalation required")
