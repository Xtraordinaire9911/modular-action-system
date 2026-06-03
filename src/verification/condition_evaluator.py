"""Small empirical condition evaluator for runtime checks.

The project plan leaves the condition language declarative. This evaluator
supports a conservative subset that is enough for fixtures and unit tests:

    device_states.thermostat_A.targetTemperature == 22
    page_state.booking.confirmed == true
    visual_state.book_button.visible
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Any, Callable

from src.contracts.types import Condition
from src.runtime.cognitive_map import CognitiveMap


@dataclass
class ConditionResult:
    condition: Condition
    passed: bool
    observed: Any = None
    expected: Any = None
    reason: str = ""


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


def evaluate_condition(condition: Condition, cognitive_map: CognitiveMap) -> ConditionResult:
    predicate = condition.predicate.strip()
    try:
        path, op, expected = _split_predicate(predicate)
        observed = _resolve_path(cognitive_map, path)
        if op is None:
            return ConditionResult(condition=condition, passed=bool(observed), observed=observed)
        passed = _OPS[op](observed, expected)
        return ConditionResult(condition=condition, passed=passed, observed=observed, expected=expected)
    except Exception as exc:
        return ConditionResult(condition=condition, passed=False, reason=str(exc))


def evaluate_all(conditions: list[Condition], cognitive_map: CognitiveMap) -> list[ConditionResult]:
    return [evaluate_condition(condition, cognitive_map) for condition in conditions]


def all_passed(results: list[ConditionResult]) -> bool:
    return all(result.passed for result in results)


def _split_predicate(predicate: str) -> tuple[str, str | None, Any]:
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op in predicate:
            left, right = predicate.split(op, 1)
            return left.strip(), op, _parse_value(right.strip())
    return predicate, None, None


def _parse_value(raw: str) -> Any:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("none", "null"):
        return None
    try:
        return ast.literal_eval(raw)
    except Exception:
        return raw.strip("\"'")


def _resolve_path(cognitive_map: CognitiveMap, path: str) -> Any:
    parts = path.split(".")
    roots: dict[str, Any] = {
        "device_states": cognitive_map.device_states,
        "page_state": cognitive_map.page_state,
        "visual_state": cognitive_map.visual_state,
    }

    if parts[0] in roots:
        value = roots[parts[0]]
        parts = parts[1:]
    else:
        value = cognitive_map.device_states

    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise KeyError(f"missing condition path: {path}")
    return value
