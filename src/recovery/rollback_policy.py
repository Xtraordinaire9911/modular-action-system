"""Tier 3 recovery: rollback to a previous safe state."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import SkillCall, SkillTuple
from src.runtime.cognitive_map import CognitiveMap


@dataclass
class RollbackDecision:
    should_rollback: bool
    rollback_call: SkillCall | None
    state_before: dict
    reason: str


class RollbackPolicy:
    def decide(self, skill_tuple: SkillTuple, cognitive_map: CognitiveMap) -> RollbackDecision:
        snapshot = cognitive_map.snapshot()
        if skill_tuple.rollback is None:
            return RollbackDecision(False, None, snapshot, "skill has no rollback spec")
        if skill_tuple.irreversible:
            return RollbackDecision(False, None, snapshot, "irreversible skill cannot be rolled back")
        rollback = SkillCall(
            skill_id=skill_tuple.rollback.skill_id,
            params=skill_tuple.rollback.params,
            priority=10,
        )
        return RollbackDecision(True, rollback, snapshot, "rollback spec available")
