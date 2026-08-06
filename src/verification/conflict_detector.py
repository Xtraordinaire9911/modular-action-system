"""Conflict detection and epistemic arbitration for mixed observations."""

from __future__ import annotations

import math
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
        max_assertion_age_ms: int | None = None,
        semantic_rules: list[SemanticConsistencyRule] | None = None,
        required_sources_by_attribute: dict[str, set[str]] | None = None,
        missing_source_mass: float = 1.0,
        fusion_strategy: Literal["rule_first", "bayesian_gate"] = "rule_first",
        bayesian_posterior_threshold: float = 0.5,
    ) -> None:
        self.numeric_tolerances = numeric_tolerances or {}
        self.halt_threshold = halt_threshold
        self.source_reliability = source_reliability or {"wot": 1.0, "dom": 0.8, "visual": 0.6, "system": 1.0}
        self.max_freshness_delta_ms = max_freshness_delta_ms
        self.max_assertion_age_ms = max_assertion_age_ms
        self.semantic_rules = semantic_rules or []
        self.required_sources_by_attribute = {
            attribute: {source.lower() for source in sources}
            for attribute, sources in (required_sources_by_attribute or {}).items()
        }
        self.missing_source_mass = max(0.0, missing_source_mass)
        self.fusion_strategy = fusion_strategy
        self.bayesian_posterior_threshold = bayesian_posterior_threshold

    def check(self, cognitive_map: CognitiveMap) -> list[Conflict]:
        conflicts, evaluated_conflict_ids, active_conflict_ids = self._candidate_conflicts(
            cognitive_map,
            min_conflict_mass=self.halt_threshold,
        )
        for conflict in conflicts:
            cognitive_map.add_conflict(conflict)
        self._resolve_inactive_conflicts(cognitive_map, evaluated_conflict_ids, active_conflict_ids)
        return conflicts

    def _candidate_conflicts(
        self,
        cognitive_map: CognitiveMap,
        *,
        min_conflict_mass: float,
    ) -> tuple[list[Conflict], set[str], set[str]]:
        conflicts: list[Conflict] = []
        active_conflict_ids: set[str] = set()
        evaluated_conflict_ids: set[str] = set()
        for (entity_id, attribute), assertions in _group_assertions(cognitive_map.state_assertions).items():
            latest_all = _latest_by_source(assertions)
            latest_by_source = self._fresh_latest_by_source(latest_all)
            evaluated_conflict_ids.add(f"{entity_id}.{attribute}")
            missing_id = f"{entity_id}.{attribute}.missing_source"
            if self._required_sources(entity_id, attribute):
                evaluated_conflict_ids.add(missing_id)
            missing = self._missing_required_sources(entity_id, attribute, latest_all, latest_by_source)
            if missing is not None and missing.conflict_mass >= min_conflict_mass:
                conflicts.append(missing)
                active_conflict_ids.add(missing.id)
            if len(latest_by_source) >= 2:
                conflict = self._compare_latest(entity_id, attribute, latest_by_source)
                if conflict and conflict.conflict_mass >= min_conflict_mass:
                    conflicts.append(conflict)
                    active_conflict_ids.add(conflict.id)
        for rule in self.semantic_rules:
            evaluated_conflict_ids.add(_semantic_rule_id(rule))
        for conflict in self._check_semantic_rules(cognitive_map):
            if conflict.conflict_mass >= min_conflict_mass:
                conflicts.append(conflict)
                active_conflict_ids.add(conflict.id)
        return conflicts, evaluated_conflict_ids, active_conflict_ids

    def _missing_required_sources(
        self,
        entity_id: str,
        attribute: str,
        latest_all: dict[str, StateAssertion],
        latest_by_source: dict[str, StateAssertion],
    ) -> Conflict | None:
        required = self._required_sources(entity_id, attribute)
        if not required or not latest_all:
            return None
        available = set(latest_by_source)
        missing = sorted(required - available)
        if not missing:
            return None
        values = {source: assertion.value for source, assertion in latest_all.items()}
        values["missing_sources"] = missing
        return Conflict(
            id=f"{entity_id}.{attribute}.missing_source",
            conflict_type="required_source_missing_or_stale",
            entity_id=entity_id,
            attribute=attribute,
            sources=sorted(required),
            values=values,
            conflict_mass=self.missing_source_mass,
            severity=_severity(self.missing_source_mass),
            description=(
                f"Required evidence unavailable for {entity_id}.{attribute}: "
                f"missing_or_stale={missing}, available={sorted(available)}"
            ),
        )

    def fuse(self, cognitive_map: CognitiveMap) -> FusionDecision:
        """Create an auditable fusion decision from current map assertions.

        This is deliberately still rule-first. It does not pretend to solve
        semantic world modelling; it exposes the selected state, support mass,
        and blocking conflicts so the runtime can explain whether System 1 may
        continue, should actively re-observe, or must halt.
        """

        if self.fusion_strategy == "bayesian_gate":
            conflicts, evaluated_conflict_ids, _ = self._candidate_conflicts(
                cognitive_map,
                min_conflict_mass=0.0001,
            )
            blocking = [
                conflict
                for conflict in conflicts
                if self._bayesian_blocking_probability(cognitive_map, conflict) >= self.bayesian_posterior_threshold
            ]
            for conflict in blocking:
                conflict.description = (
                    f"{conflict.description}; bayesian_posterior="
                    f"{self._bayesian_blocking_probability(cognitive_map, conflict):.3f}"
                )
                cognitive_map.add_conflict(conflict)
            self._resolve_inactive_conflicts(
                cognitive_map,
                evaluated_conflict_ids,
                {conflict.id for conflict in blocking},
            )
        else:
            conflicts = self.check(cognitive_map)
            blocking = [conflict for conflict in conflicts if conflict.conflict_mass >= self.halt_threshold]
        fused_states: list[FusedState] = []
        for (entity_id, attribute), assertions in _group_assertions(cognitive_map.state_assertions).items():
            latest_by_source = self._fresh_latest_by_source(_latest_by_source(assertions))
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
            reason = "blocking sensory conflict requires active perception or escalation"
            if self.fusion_strategy == "bayesian_gate":
                strongest = max(blocking, key=lambda conflict: self._bayesian_blocking_probability(cognitive_map, conflict))
                reason = (
                    "bayesian_gate posterior "
                    f"{self._bayesian_blocking_probability(cognitive_map, strongest):.3f} "
                    "requires active perception or escalation"
                )
            decision = FusionDecision(
                allow_system1=False,
                reason=reason,
                fused_states=fused_states,
                conflicts=blocking,
                active_perception_required=True,
            )
        else:
            decision = FusionDecision(
                allow_system1=True,
                reason="no blocking sensory conflict",
                fused_states=fused_states,
                conflicts=conflicts,
                active_perception_required=False,
            )
        cognitive_map.apply_fusion_decision(decision.fused_states, allow_system1=decision.allow_system1)
        return decision

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

    def _bayesian_blocking_probability(self, cognitive_map: CognitiveMap, conflict: Conflict) -> float:
        latest = self._latest_for_conflict(cognitive_map, conflict)
        timestamps = [assertion.timestamp_ms for assertion in latest.values()]
        staleness_ms = float(max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0.0
        dom_reliability = self.source_reliability.get("dom", 0.5)
        wot_reliability = self.source_reliability.get("wot", 0.5)
        missing_probability = 0.0
        if conflict.conflict_type == "required_source_missing_or_stale":
            missing_probability = min(1.0, 0.35 + 0.35 * len(conflict.values.get("missing_sources", [])))
        logit = (
            -2.2
            + 2.8 * float(conflict.conflict_mass)
            + 2.0 * missing_probability
            + 1.2 * min(staleness_ms / 1000.0, 2.0)
            + 1.4 * (1.0 - wot_reliability)
            - 0.8 * max(0.0, wot_reliability - dom_reliability)
        )
        return _sigmoid(logit)

    def _latest_for_conflict(self, cognitive_map: CognitiveMap, conflict: Conflict) -> dict[str, StateAssertion]:
        assertions = [
            assertion
            for assertion in cognitive_map.state_assertions
            if assertion.entity_id == conflict.entity_id and assertion.attribute == conflict.attribute
        ]
        return self._fresh_latest_by_source(_latest_by_source(assertions))

    def _fresh_latest_by_source(
        self,
        latest_by_source: dict[str, StateAssertion],
    ) -> dict[str, StateAssertion]:
        if not latest_by_source:
            return {}
        newest_timestamp = max(assertion.timestamp_ms for assertion in latest_by_source.values())
        relative_fresh = {
            source: assertion
            for source, assertion in latest_by_source.items()
            if self.max_freshness_delta_ms <= 0
            or newest_timestamp - assertion.timestamp_ms <= self.max_freshness_delta_ms
        }
        if self.max_assertion_age_ms is None:
            return relative_fresh
        now_ms = _now_ms()
        return {
            source: assertion
            for source, assertion in relative_fresh.items()
            if now_ms - assertion.timestamp_ms <= self.max_assertion_age_ms
        }

    def _required_sources(self, entity_id: str, attribute: str) -> set[str]:
        required = self.required_sources_by_attribute.get(f"{entity_id}.{attribute}")
        if required is None:
            required = self.required_sources_by_attribute.get(attribute)
        return set(required or set())

    def _resolve_inactive_conflicts(
        self,
        cognitive_map: CognitiveMap,
        evaluated_conflict_ids: set[str],
        active_conflict_ids: set[str],
    ) -> None:
        for conflict in cognitive_map.conflicts:
            if conflict.resolved or conflict.id not in evaluated_conflict_ids or conflict.id in active_conflict_ids:
                continue
            conflict.resolved = True
            conflict.decision = "fresh_evidence_resolved"
        if evaluated_conflict_ids:
            cognitive_map.touch()

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
                        id=_semantic_rule_id(rule),
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


def _semantic_rule_id(rule: SemanticConsistencyRule) -> str:
    return f"semantic.{rule.conflict_type}"


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


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


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
