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
from src.runtime.cognitive_map import CognitiveMap, canonical_state_name


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
        parts = _split_compound(predicate)
        if len(parts) > 1:
            results = [evaluate_condition(Condition(part, condition.description), cognitive_map) for part in parts]
            return ConditionResult(
                condition=condition,
                passed=all_passed(results),
                observed=[result.observed for result in results],
                expected=[result.expected for result in results],
                reason="; ".join(result.reason for result in results if result.reason),
            )

        path, op, expected_raw = _split_predicate(predicate)
        observed = _resolve_path(cognitive_map, path)
        if op is None:
            return ConditionResult(condition=condition, passed=bool(observed), observed=observed)
        expected = _resolve_value(cognitive_map, expected_raw)
        passed = _OPS[op](observed, expected)
        return ConditionResult(condition=condition, passed=passed, observed=observed, expected=expected)
    except Exception as exc:
        return ConditionResult(condition=condition, passed=False, reason=str(exc))


def evaluate_all(conditions: list[Condition], cognitive_map: CognitiveMap) -> list[ConditionResult]:
    return [evaluate_condition(condition, cognitive_map) for condition in conditions]


def all_passed(results: list[ConditionResult]) -> bool:
    return all(result.passed for result in results)


def _split_compound(predicate: str) -> list[str]:
    return [part.strip() for part in predicate.split(" and ") if part.strip()]


def _split_predicate(predicate: str) -> tuple[str, str | None, str]:
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op in predicate:
            left, right = predicate.split(op, 1)
            return left.strip(), op, right.strip()
    return predicate, None, ""


def _resolve_value(cognitive_map: CognitiveMap, raw: str) -> Any:
    if _looks_like_path(raw):
        try:
            return _resolve_path(cognitive_map, raw)
        except KeyError:
            pass
    return _parse_value(raw)


def _looks_like_path(raw: str) -> bool:
    lowered = raw.lower()
    if lowered in ("true", "false", "none", "null"):
        return False
    try:
        ast.literal_eval(raw)
        return False
    except Exception:
        return raw.replace("_", "").replace(".", "").isalnum()


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
        "fused_state": cognitive_map.fused_state,
        "device_states": cognitive_map.device_states,
        "page_state": cognitive_map.page_state,
        "visual_state": cognitive_map.visual_state,
        "params": cognitive_map.current_skill.params if cognitive_map.current_skill else {},
    }

    if parts[0] in roots:
        value = roots[parts[0]]
        parts = parts[1:]
    elif cognitive_map.current_skill and parts[0] in cognitive_map.current_skill.params:
        value = cognitive_map.current_skill.params[parts[0]]
        parts = parts[1:]
    else:
        try:
            return _walk_path(cognitive_map.fused_state, parts, path)
        except KeyError:
            value = cognitive_map.device_states

    return _walk_path(value, parts, path)


def _walk_path(value: Any, parts: list[str], original_path: str) -> Any:
    for part in parts:
        if not isinstance(value, dict):
            raise KeyError(f"missing condition path: {original_path}")
        if part in value:
            value = value[part]
            continue
        normalized = canonical_state_name(part)
        if normalized in value:
            value = value[normalized]
            continue
        raise KeyError(f"missing condition path: {original_path}")
    return value
