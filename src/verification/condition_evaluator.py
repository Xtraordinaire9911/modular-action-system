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


_CANONICAL_PATH_ALIASES: dict[str, list[str]] = {
    "booking_status": [
        "device_states.booking_status",
        "page_state.booking_status",
    ],
    "booking.confirmed": [
        "device_states.booking_confirmed",
        "page_state.booking_confirmed",
    ],
    "thermostat.target_temperature": [
        "device_states.thermostat.target_temperature",
        "device_states.thermostat.targetTemperature",
        "device_states.thermostat_A.target_temperature",
        "device_states.thermostat_A.targetTemperature",
    ],
    "thermostat.current_temperature": [
        "device_states.thermostat.current_temperature",
        "device_states.thermostat.currentTemperature",
        "device_states.thermostat_A.current_temperature",
        "device_states.thermostat_A.currentTemperature",
    ],
    "lighting.brightness": [
        "device_states.lighting.brightness",
        "device_states.lights.brightness",
        "device_states.lighting_A.brightness",
        "device_states.lights_A.brightness",
    ],
    "projector.power": [
        "device_states.projector.power",
        "device_states.projector_A.power",
    ],
    "readiness.ready": [
        "device_states.readiness.ready",
        "page_state.readiness.ready",
    ],
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
    # Resolve in a deterministic order: explicit roots, params, canonical aliases,
    # then legacy implicit device-state traversal.
    explicit = _resolve_explicit_path(cognitive_map, path)
    if explicit[0]:
        return explicit[1]

    if cognitive_map.current_skill and path in cognitive_map.current_skill.params:
        return cognitive_map.current_skill.params[path]

    if path in _CANONICAL_PATH_ALIASES:
        for alias in _CANONICAL_PATH_ALIASES[path]:
            resolved = _resolve_explicit_path(cognitive_map, alias)
            if resolved[0]:
                return resolved[1]
        tried = ", ".join(_CANONICAL_PATH_ALIASES[path])
        raise KeyError(f"missing canonical condition path: {path}; tried: {tried}")

    fallback = _resolve_implicit_device_path(cognitive_map, path)
    if fallback[0]:
        return fallback[1]

    raise KeyError(f"missing condition path: {path}")


def _resolve_explicit_path(cognitive_map: CognitiveMap, path: str) -> tuple[bool, Any]:
    parts = path.split(".")
    roots: dict[str, Any] = {
        "device_states": cognitive_map.device_states,
        "page_state": cognitive_map.page_state,
        "visual_state": cognitive_map.visual_state,
        "params": cognitive_map.current_skill.params if cognitive_map.current_skill else {},
    }
    if not parts or parts[0] not in roots:
        return False, None

    value: Any = roots[parts[0]]
    for part in parts[1:]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return False, None
    return True, value


def _resolve_implicit_device_path(cognitive_map: CognitiveMap, path: str) -> tuple[bool, Any]:
    value: Any = cognitive_map.device_states
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return False, None
    return True, value
