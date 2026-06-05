"""Conflict detection and simple arbitration for mixed observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.runtime.cognitive_map import CognitiveMap, Conflict


@dataclass
class ConflictRule:
    conflict_type: str
    left_source: str
    left_path: str
    right_source: str
    right_path: str


class ConflictDetector:
    """Detect disagreements between state sources in the CognitiveMap."""

    def detect(self, cognitive_map: CognitiveMap, rules: list[ConflictRule]) -> list[Conflict]:
        detected: list[Conflict] = []
        for rule in rules:
            left = _resolve(cognitive_map, rule.left_source, rule.left_path)
            right = _resolve(cognitive_map, rule.right_source, rule.right_path)
            if left is not None and right is not None and left != right:
                detected.append(
                    cognitive_map.mark_conflict(
                        conflict_type=rule.conflict_type,
                        sources=[rule.left_source, rule.right_source],
                        description=f"{rule.left_path}={left!r} differs from {rule.right_path}={right!r}",
                    )
                )
        return detected

    def arbitrate(self, conflict: Conflict, decision: str = "request_cross_backend_verification") -> Conflict:
        conflict.decision = decision
        conflict.resolved = decision not in ("pause", "escalate_human")
        return conflict


def _resolve(cognitive_map: CognitiveMap, source: str, path: str) -> Any:
    root = {
        "device": cognitive_map.device_states,
        "device_states": cognitive_map.device_states,
        "page": cognitive_map.page_state,
        "page_state": cognitive_map.page_state,
        "visual": cognitive_map.visual_state,
        "visual_state": cognitive_map.visual_state,
    }.get(source)
    if root is None:
        return None
    value: Any = root
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value
