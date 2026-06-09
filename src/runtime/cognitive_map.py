"""
The CognitiveMap keeps the task-relevant state that verification, recovery,
and safety decisions need while the low-level executors remain backend-owned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall


@dataclass
class Conflict:
    """A disagreement between two observation sources."""

    conflict_type: str
    sources: list[str]
    description: str
    resolved: bool = False
    decision: str | None = None


@dataclass
class CognitiveMap:
    """Shared runtime state for one task episode."""

    task_id: str
    timestamp: float = field(default_factory=time.time)
    current_skill: SkillCall | None = None
    affordances: list[Affordance] = field(default_factory=list)
    device_states: dict[str, Any] = field(default_factory=dict)
    page_state: dict[str, Any] = field(default_factory=dict)
    visual_state: dict[str, Any] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    execution_history: list[ExecutionResult] = field(default_factory=list)

    def set_current_skill(self, skill_call: SkillCall | None) -> None:
        self.current_skill = skill_call
        self.touch()

    def update_affordances(self, affordances: list[Affordance]) -> None:
        self.affordances = affordances
        self.touch()

    def update_from_observation(self, observation: Observation) -> None:
        """Merge observed state into the map.

        Observation currently exposes device states directly. Page and visual
        state are represented through the accessibility tree when callers have
        structured fields to pass before a richer schema is available.
        """
        if observation.device_states:
            _deep_merge(self.device_states, observation.device_states)

        tree = observation.accessibility_tree or {}
        if isinstance(tree.get("page_state"), dict):
            _deep_merge(self.page_state, tree["page_state"])
        if isinstance(tree.get("visual_state"), dict):
            _deep_merge(self.visual_state, tree["visual_state"])

        self.touch()

    def record_execution_result(self, result: ExecutionResult) -> None:
        self.execution_history.append(result)
        if result.raw_observation_delta:
            _deep_merge(self.device_states, result.raw_observation_delta)
        self.touch()

    def mark_conflict(
        self,
        conflict_type: str,
        sources: list[str],
        description: str,
    ) -> Conflict:
        conflict = Conflict(conflict_type=conflict_type, sources=sources, description=description)
        self.conflicts.append(conflict)
        self.touch()
        return conflict

    def unresolved_conflicts(self) -> list[Conflict]:
        return [conflict for conflict in self.conflicts if not conflict.resolved]

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "current_skill": self.current_skill.skill_id if self.current_skill else None,
            "device_states": self.device_states,
            "page_state": self.page_state,
            "visual_state": self.visual_state,
            "conflicts": [conflict.__dict__ for conflict in self.conflicts],
            "execution_history": [result.__dict__ for result in self.execution_history],
        }

    def touch(self) -> None:
        self.timestamp = time.time()


def _deep_merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
