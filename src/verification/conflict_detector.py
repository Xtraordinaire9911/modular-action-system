"""Conflict detection and epistemic arbitration for mixed observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.runtime.cognitive_map import CognitiveMap, Conflict, StateAssertion

Severity = Literal["low", "medium", "high"]


@dataclass
class SemanticConsistencyRule:
    conflict_type: str
    entity_id: str
    attribute: str
    value: Any
    depends_on_entity_id: str
    depends_on_attribute: str
    depends_on_value: Any
    severity: Severity = "high"


@dataclass
class ConflictRule:
    conflict_type: str
    left_source: str
    left_path: str
    right_source: str
    right_path: str


@dataclass
class FusedState:
    entity_id: str
    attribute: str
    value: Any
    support: dict[str, float]
    selected_support: float
    confidence: float
    sources: list[str]


@dataclass
class FusionDecision:
    allow_system1: bool
    reason: str
    fused_states: list[FusedState]
    conflicts: list[Conflict]
    active_perception_required: bool = False


class SensoryConflictError(Exception):
    def __init__(
        self,
        entity_id: str,
        attribute: str,
        values: dict[str, object],
        conflict_mass: float,
        message: str = "Sensory conflict detected",
    ) -> None:
        self.entity_id = entity_id
        self.attribute = attribute
        self.values = values
        self.conflict_mass = conflict_mass
        super().__init__(message)


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


class EpistemicArbiter:
    """Fusion gate that detects cross-source state contradictions."""

    def __init__(
        self,
        numeric_tolerances: dict[str, float] | None = None,
        halt_threshold: float = 1.0,
        source_reliability: dict[str, float] | None = None,
        max_freshness_delta_ms: int = 5000,
        semantic_rules: list[SemanticConsistencyRule] | None = None,
    ) -> None:
        self.numeric_tolerances = numeric_tolerances or {}
        self.halt_threshold = halt_threshold
        self.source_reliability = source_reliability or {"wot": 1.0, "dom": 0.8, "visual": 0.6, "system": 1.0}
        self.max_freshness_delta_ms = max_freshness_delta_ms
        self.semantic_rules = semantic_rules or []

    def check(self, cognitive_map: CognitiveMap) -> list[Conflict]:
        conflicts: list[Conflict] = []
        for (entity_id, attribute), assertions in _group_assertions(cognitive_map.state_assertions).items():
            latest_by_source = _latest_by_source(assertions)
            if len(latest_by_source) < 2:
                continue
            conflict = self._compare_latest(entity_id, attribute, latest_by_source)
            if conflict and conflict.conflict_mass >= self.halt_threshold:
                cognitive_map.add_conflict(conflict)
                conflicts.append(conflict)
        for conflict in self._check_semantic_rules(cognitive_map):
            if conflict.conflict_mass >= self.halt_threshold:
                cognitive_map.add_conflict(conflict)
                conflicts.append(conflict)
        return conflicts

    def fuse(self, cognitive_map: CognitiveMap) -> FusionDecision:
        """Create an auditable fusion decision from current map assertions.

        This is deliberately still rule-first. It does not pretend to solve
        semantic world modelling; it exposes the selected state, support mass,
        and blocking conflicts so the runtime can explain whether System 1 may
        continue, should actively re-observe, or must halt.
        """

        conflicts = self.check(cognitive_map)
        blocking = [conflict for conflict in conflicts if conflict.conflict_mass >= self.halt_threshold]
        fused_states: list[FusedState] = []
        for (entity_id, attribute), assertions in _group_assertions(cognitive_map.state_assertions).items():
            latest_by_source = _latest_by_source(assertions)
            if not latest_by_source:
                continue
            newest_timestamp = max(assertion.timestamp_ms for assertion in latest_by_source.values())
            support = self._support_by_value(latest_by_source, newest_timestamp)
            if not support:
                continue
            selected_key, selected_support = max(support.items(), key=lambda item: item[1])
            selected_assertions = [
                assertion for assertion in latest_by_source.values() if _value_key(assertion.value) == selected_key
            ]
            total_support = sum(support.values()) or 1.0
            selected = max(
                selected_assertions,
                key=lambda assertion: self._evidence_weight(assertion, newest_timestamp),
            )
            fused_states.append(
                FusedState(
                    entity_id=entity_id,
                    attribute=attribute,
                    value=selected.value,
                    support=support,
                    selected_support=selected_support,
                    confidence=min(1.0, selected_support / total_support),
                    sources=[assertion.source for assertion in selected_assertions],
                )
            )

        if blocking:
            return FusionDecision(
                allow_system1=False,
                reason="blocking sensory conflict requires active perception or escalation",
                fused_states=fused_states,
                conflicts=blocking,
                active_perception_required=True,
            )
        return FusionDecision(
            allow_system1=True,
            reason="no blocking sensory conflict",
            fused_states=fused_states,
            conflicts=conflicts,
            active_perception_required=False,
        )

    def should_halt_system1(self, conflicts: list[Conflict]) -> bool:
        return any(conflict.conflict_mass >= self.halt_threshold for conflict in conflicts)

    def assert_no_blocking_conflict(self, cognitive_map: CognitiveMap) -> None:
        conflicts = self.check(cognitive_map)
        if self.should_halt_system1(conflicts):
            conflict = max(conflicts, key=lambda item: item.conflict_mass)
            raise SensoryConflictError(
                entity_id=conflict.entity_id,
                attribute=conflict.attribute,
                values=conflict.values,
                conflict_mass=conflict.conflict_mass,
            )

    def _compare_latest(
        self,
        entity_id: str,
        attribute: str,
        latest_by_source: dict[str, StateAssertion],
    ) -> Conflict | None:
        values = {source: assertion.value for source, assertion in latest_by_source.items()}
        sources = list(values)
        newest_timestamp = max(assertion.timestamp_ms for assertion in latest_by_source.values())
        support_by_value = self._support_by_value(latest_by_source, newest_timestamp)
        max_mass = 0.0
        for i, left_source in enumerate(sources):
            for right_source in sources[i + 1 :]:
                left = latest_by_source[left_source]
                right = latest_by_source[right_source]
                mass = self._conflict_mass(attribute, left, right, newest_timestamp)
                max_mass = max(max_mass, mass)
        if max_mass <= 0:
            return None
        if len(sources) > 2:
            max_mass *= _minority_majority_ratio(support_by_value)
        if max_mass <= 0:
            return None
        return Conflict(
            id=f"{entity_id}.{attribute}",
            conflict_type=f"{attribute}_mismatch",
            entity_id=entity_id,
            attribute=attribute,
            sources=sources,
            values=values,
            conflict_mass=max_mass,
            severity=_severity(max_mass),
            description=(
                f"Conflicting {attribute} assertions for {entity_id}: {values}; " f"support={support_by_value}"
            ),
        )

    def _conflict_mass(
        self,
        attribute: str,
        left: StateAssertion,
        right: StateAssertion,
        newest_timestamp: int,
    ) -> float:
        base = _value_distance(attribute, left.value, right.value, self.numeric_tolerances)
        if base <= 0:
            return 0.0
        return base * self._pair_evidence_weight(left, right, newest_timestamp)

    def _pair_evidence_weight(
        self,
        left: StateAssertion,
        right: StateAssertion,
        newest_timestamp: int,
    ) -> float:
        source_weight = max(
            self.source_reliability.get(left.source, 0.5),
            self.source_reliability.get(right.source, 0.5),
        )
        confidence = min(left.confidence, right.confidence)
        freshness = min(
            _freshness_weight(newest_timestamp - left.timestamp_ms, self.max_freshness_delta_ms),
            _freshness_weight(newest_timestamp - right.timestamp_ms, self.max_freshness_delta_ms),
        )
        return source_weight * confidence * freshness

    def _evidence_weight(self, assertion: StateAssertion, newest_timestamp: int) -> float:
        source_weight = self.source_reliability.get(assertion.source, 0.5)
        freshness = _freshness_weight(
            newest_timestamp - assertion.timestamp_ms,
            self.max_freshness_delta_ms,
        )
        return source_weight * assertion.confidence * freshness

    def _support_by_value(
        self,
        latest_by_source: dict[str, StateAssertion],
        newest_timestamp: int,
    ) -> dict[str, float]:
        support: dict[str, float] = {}
        for assertion in latest_by_source.values():
            key = _value_key(assertion.value)
            support[key] = support.get(key, 0.0) + self._evidence_weight(assertion, newest_timestamp)
        return support

    def _check_semantic_rules(self, cognitive_map: CognitiveMap) -> list[Conflict]:
        conflicts: list[Conflict] = []
        for rule in self.semantic_rules:
            primary = cognitive_map.get_latest_state(rule.entity_id, rule.attribute)
            dependency = cognitive_map.get_latest_state(
                rule.depends_on_entity_id,
                rule.depends_on_attribute,
            )
            if primary is None or dependency is None:
                continue
            if primary.value == rule.value and dependency.value == rule.depends_on_value:
                mass = 2.0 if rule.severity == "high" else 1.0
                conflicts.append(
                    Conflict(
                        id=f"semantic.{rule.conflict_type}",
                        conflict_type=rule.conflict_type,
                        entity_id=rule.entity_id,
                        attribute=rule.attribute,
                        sources=[primary.source, dependency.source],
                        values={
                            f"{rule.entity_id}.{rule.attribute}": primary.value,
                            f"{rule.depends_on_entity_id}.{rule.depends_on_attribute}": dependency.value,
                        },
                        conflict_mass=mass,
                        severity=rule.severity,
                        description=(
                            f"Semantic conflict: {rule.entity_id}.{rule.attribute}={primary.value!r} "
                            f"while {rule.depends_on_entity_id}.{rule.depends_on_attribute}="
                            f"{dependency.value!r}"
                        ),
                    )
                )
        return conflicts


def _value_distance(attribute: str, left: Any, right: Any, numeric_tolerances: dict[str, float]) -> float:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        tolerance = numeric_tolerances.get(attribute, 1.0)
        if tolerance <= 0:
            tolerance = 1.0
        return abs(float(left) - float(right)) / tolerance
    return 0.0 if left == right else 1.0


def _freshness_weight(age_delta_ms: int, max_freshness_delta_ms: int) -> float:
    if max_freshness_delta_ms <= 0:
        return 1.0
    if age_delta_ms <= max_freshness_delta_ms:
        return 1.0
    return max(0.25, max_freshness_delta_ms / age_delta_ms)


def _value_key(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return repr(value)


def _minority_majority_ratio(support_by_value: dict[str, float]) -> float:
    if len(support_by_value) < 2:
        return 0.0
    ranked = sorted(support_by_value.values(), reverse=True)
    majority = ranked[0]
    minority = ranked[1]
    if majority <= 0:
        return 0.0
    return min(1.0, minority / majority)


def _group_assertions(assertions: list[StateAssertion]) -> dict[tuple[str, str], list[StateAssertion]]:
    grouped: dict[tuple[str, str], list[StateAssertion]] = {}
    for assertion in assertions:
        grouped.setdefault((assertion.entity_id, assertion.attribute), []).append(assertion)
    return grouped


def _latest_by_source(assertions: list[StateAssertion]) -> dict[str, StateAssertion]:
    latest: dict[str, StateAssertion] = {}
    for assertion in assertions:
        current = latest.get(assertion.source)
        if current is None or assertion.timestamp_ms >= current.timestamp_ms:
            latest[assertion.source] = assertion
    return latest


def _severity(conflict_mass: float) -> Severity:
    if conflict_mass >= 2.0:
        return "high"
    if conflict_mass >= 1.0:
        return "medium"
    return "low"


def _resolve(cognitive_map: CognitiveMap, source: str, path: str) -> Any:
    root = {
        "device": cognitive_map.device_states,
        "device_states": cognitive_map.device_states,
        "wot": cognitive_map.device_states,
        "dom": cognitive_map.page_state,
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
