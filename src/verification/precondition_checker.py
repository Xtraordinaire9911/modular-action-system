"""Runtime precondition checks """

from __future__ import annotations

from src.contracts.types import Condition
from src.runtime.cognitive_map import CognitiveMap
from src.verification.condition_evaluator import ConditionResult, all_passed, evaluate_all


class PreconditionChecker:
    def check(self, conditions: list[Condition], cognitive_map: CognitiveMap) -> list[ConditionResult]:
        return evaluate_all(conditions, cognitive_map)

    def passes(self, conditions: list[Condition], cognitive_map: CognitiveMap) -> bool:
        return all_passed(self.check(conditions, cognitive_map))
