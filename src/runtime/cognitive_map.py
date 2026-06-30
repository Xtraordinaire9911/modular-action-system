"""
The CognitiveMap keeps the task-relevant state that verification, recovery,
and safety decisions need while the low-level executors remain backend-owned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from src.contracts.types import Affordance as ContractAffordance
from src.contracts.types import ExecutionResult, Observation, SkillCall

SourceType = Literal["dom", "visual", "wot", "system"]


@dataclass
class Entity:
    id: str
    type: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeAffordance:
    id: str
    source: SourceType
    entity_id: str
    action_name: str
    action_type: str
    confidence: float
    grounding: dict[str, Any]
    input_schema: dict[str, Any] | None = None
    skill_names: list[str] = field(default_factory=list)


@dataclass
class StateAssertion:
    entity_id: str
    attribute: str
    value: Any
    source: SourceType
    confidence: float = 1.0
    timestamp_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conflict:
    """A disagreement among observation sources, including two-source and multi-source conflicts."""

    conflict_type: str
    sources: list[str]
    description: str
    resolved: bool = False
    decision: str | None = None
    id: str = ""
    entity_id: str = ""
    attribute: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    conflict_mass: float = 0.0
    severity: Literal["low", "medium", "high"] = "low"


@dataclass
class CognitiveMap:
    """Shared runtime state for one task episode."""

    task_id: str
    timestamp: float = field(default_factory=time.time)
    current_skill: SkillCall | None = None
    affordances: list[ContractAffordance] = field(default_factory=list)
    entities: dict[str, Entity] = field(default_factory=dict)
    runtime_affordances: dict[str, RuntimeAffordance] = field(default_factory=dict)
    state_assertions: list[StateAssertion] = field(default_factory=list)
    device_states: dict[str, Any] = field(default_factory=dict)
    page_state: dict[str, Any] = field(default_factory=dict)
    visual_state: dict[str, Any] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    execution_history: list[ExecutionResult] = field(default_factory=list)

    def set_current_skill(self, skill_call: SkillCall | None) -> None:
        self.current_skill = skill_call
        self.touch()

    def update_affordances(self, affordances: list[ContractAffordance]) -> None:
        runtime_affordances = [_runtime_affordance_from_contract(affordance) for affordance in affordances]
        for affordance in runtime_affordances:
            _validate_runtime_affordance(affordance)
        self.affordances = affordances
        for affordance in runtime_affordances:
            affordance.confidence = _clamp_confidence(affordance.confidence)
            self.runtime_affordances[affordance.id] = affordance
        self.touch()

    def add_entity(self, entity: Entity) -> None:
        if not entity.id.strip():
            raise ValueError("entity.id must be non-empty")
        if not entity.type.strip():
            raise ValueError("entity.type must be non-empty")
        self.entities[entity.id] = entity
        self.touch()

    def add_affordance(self, affordance: RuntimeAffordance) -> None:
        _validate_runtime_affordance(affordance)
        affordance.confidence = _clamp_confidence(affordance.confidence)
        self.runtime_affordances[affordance.id] = affordance
        self.touch()

    def add_state_assertion(self, assertion: StateAssertion) -> None:
        if not assertion.entity_id.strip():
            raise ValueError("assertion.entity_id must be non-empty")
        if not assertion.attribute.strip():
            raise ValueError("assertion.attribute must be non-empty")
        assertion.confidence = _clamp_confidence(assertion.confidence)
        if assertion.timestamp_ms == 0:
            assertion.timestamp_ms = int(time.time() * 1000)
        self.state_assertions.append(assertion)
        self.entities.setdefault(assertion.entity_id, Entity(id=assertion.entity_id, type="unknown"))
        self._mirror_assertion(assertion)
        self.touch()

    def get_latest_state(
        self,
        entity_id: str,
        attribute: str,
        source: str | None = None,
    ) -> StateAssertion | None:
        matches = [
            assertion
            for assertion in self.state_assertions
            if assertion.entity_id == entity_id
            and assertion.attribute == attribute
            and (source is None or assertion.source == source)
        ]
        if not matches:
            return None
        return max(matches, key=lambda assertion: assertion.timestamp_ms)

    def get_affordances_for_skill(self, skill_name: str) -> list[RuntimeAffordance]:
        return [
            affordance
            for affordance in self.runtime_affordances.values()
            if skill_name in affordance.skill_names or affordance.action_name == skill_name
        ]

    def add_conflict(self, conflict: Conflict) -> None:
        if conflict.id == "":
            conflict.id = f"conflict_{len(self.conflicts) + 1}"
        for index, existing in enumerate(self.conflicts):
            if existing.id == conflict.id:
                self.conflicts[index] = conflict
                self.touch()
                return
        self.conflicts.append(conflict)
        self.touch()

    def update_from_observation(self, observation: Observation) -> None:
        """Merge observed state into the map.

        Observation currently exposes device states directly. Page and visual
        state are represented through the accessibility tree when callers have
        structured fields to pass before a richer schema is available.
        """
        if observation.device_states:
            _deep_merge(self.device_states, observation.device_states)
            self._ingest_state_tree(observation.device_states, source="wot")

        tree = observation.accessibility_tree or {}
        if isinstance(tree.get("page_state"), dict):
            _deep_merge(self.page_state, tree["page_state"])
            self._ingest_state_tree(tree["page_state"], source="dom")
        if isinstance(tree.get("visual_state"), dict):
            _deep_merge(self.visual_state, tree["visual_state"])
            self._ingest_state_tree(tree["visual_state"], source="visual")

        self.touch()

    def record_execution_result(self, result: ExecutionResult) -> None:
        self.execution_history.append(result)
        if result.raw_observation_delta:
            _deep_merge(self.device_states, result.raw_observation_delta)
            self._ingest_state_tree(result.raw_observation_delta, source="wot")
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
            "entities": {entity_id: entity.__dict__ for entity_id, entity in self.entities.items()},
            "runtime_affordances": {
                affordance_id: affordance.__dict__ for affordance_id, affordance in self.runtime_affordances.items()
            },
            "state_assertions": [assertion.__dict__ for assertion in self.state_assertions],
            "device_states": self.device_states,
            "page_state": self.page_state,
            "visual_state": self.visual_state,
            "conflicts": [conflict.__dict__ for conflict in self.conflicts],
            "execution_history": [result.__dict__ for result in self.execution_history],
        }

    def touch(self) -> None:
        self.timestamp = time.time()

    def _mirror_assertion(self, assertion: StateAssertion) -> None:
        target = {
            "dom": self.page_state,
            "visual": self.visual_state,
            "wot": self.device_states,
            "system": self.device_states,
        }[assertion.source]
        entity_state = target.setdefault(assertion.entity_id, {})
        if isinstance(entity_state, dict):
            entity_state[assertion.attribute] = assertion.value

    def _ingest_state_tree(self, state_tree: dict[str, Any], source: SourceType) -> None:
        now = int(time.time() * 1000)
        for entity_id, values in state_tree.items():
            if isinstance(values, dict):
                self.entities.setdefault(entity_id, Entity(id=entity_id, type="unknown"))
                for attribute, value in values.items():
                    self.state_assertions.append(
                        StateAssertion(
                            entity_id=entity_id,
                            attribute=attribute,
                            value=value,
                            source=source,
                            confidence=1.0,
                            timestamp_ms=now,
                        )
                    )


def _clamp_confidence(confidence: float) -> float:
    return max(0.0, min(1.0, confidence))


def _validate_runtime_affordance(affordance: RuntimeAffordance) -> None:
    if not affordance.id.strip():
        raise ValueError("affordance.id must be non-empty")
    if not affordance.entity_id.strip():
        raise ValueError("affordance.entity_id must be non-empty")
    if not affordance.action_name.strip():
        raise ValueError("affordance.action_name must be non-empty")


def _runtime_affordance_from_contract(affordance: ContractAffordance) -> RuntimeAffordance:
    source: SourceType
    if affordance.source == "DOM":
        source = "dom"
    elif affordance.source == "VISUAL":
        source = "visual"
    else:
        source = "wot"
    locator = dict(affordance.locator)
    state = dict(affordance.state)
    action_name = str(locator.get("skill_id") or state.get("skill_id") or affordance.action)
    skill_names = [action_name]
    for candidate in (locator.get("skill_name"), state.get("skill_name")):
        if isinstance(candidate, str) and candidate not in skill_names:
            skill_names.append(candidate)
    entity_id = str(
        locator.get("entity_id")
        or locator.get("thing_id")
        or locator.get("target_id")
        or state.get("entity_id")
        or affordance.id
    )
    return RuntimeAffordance(
        id=affordance.id,
        source=source,
        entity_id=entity_id,
        action_name=action_name,
        action_type=affordance.type,
        confidence=affordance.confidence,
        grounding={**locator, "label": affordance.label, "safety_level": affordance.safety_level},
        input_schema=state.get("input_schema") if isinstance(state.get("input_schema"), dict) else None,
        skill_names=skill_names,
    )


def _deep_merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
