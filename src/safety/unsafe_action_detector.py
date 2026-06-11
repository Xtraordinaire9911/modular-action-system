"""Safety enforcement for runtime actions."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import SkillCall, SkillTuple


@dataclass
class SafetyDecision:
    action: str
    allowed: bool
    requires_human_confirmation: bool
    reason: str


class UnsafeActionDetector:
    def decide(
        self,
        skill_call: SkillCall,
        skill_tuple: SkillTuple,
        human_confirmed: bool = False,
    ) -> SafetyDecision:
        if skill_tuple.irreversible and not human_confirmed:
            return SafetyDecision(
                action=skill_call.skill_id,
                allowed=False,
                requires_human_confirmation=True,
                reason="irreversible action requires confirmation",
            )
        if skill_tuple.safety_level == "high" and not human_confirmed:
            return SafetyDecision(
                action=skill_call.skill_id,
                allowed=False,
                requires_human_confirmation=True,
                reason="high-risk action requires confirmation",
            )
        return SafetyDecision(
            action=skill_call.skill_id,
            allowed=True,
            requires_human_confirmation=False,
            reason="action allowed",
        )
