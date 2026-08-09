"""Planner-facing Semantic Scene Graph view over the runtime CognitiveMap.

The runtime map is the only mutable state store.  This module derives a
read-only graph for planner prompts and compatibility APIs; it never maintains
an independent copy of episode state.
"""

from __future__ import annotations

from typing import Iterable

from src.contracts.types import (
    Affordance,
    Observation,
    SemanticSceneGraph,
    SemanticSceneGraphEdge,
    SemanticSceneGraphNode,
)
from src.runtime.cognitive_map import (
    CognitiveMap,
    RuntimeAffordance,
    StateAssertion,
    canonical_state_name,
)


class SemanticSceneGraphViewBuilder:
    """Build planner views from the canonical runtime CognitiveMap."""

    def build_runtime_map(
        self,
        observation: Observation,
        affordances: Iterable[Affordance] = (),
        task_id: str = "task",
    ) -> CognitiveMap:
        cognitive_map = CognitiveMap(task_id=task_id)
        materialized = list(affordances)
        if materialized:
            cognitive_map.update_affordances(materialized)
        cognitive_map.update_from_observation(observation)
        return cognitive_map

    def build(
        self,
        observation: Observation,
        affordances: Iterable[Affordance] = (),
        task_id: str = "task",
    ) -> SemanticSceneGraph:
        return self.build_from_map(self.build_runtime_map(observation, affordances, task_id))

    def build_from_map(self, cognitive_map: CognitiveMap) -> SemanticSceneGraph:
        graph = SemanticSceneGraph(task_id=cognitive_map.task_id)
        node_index: dict[str, SemanticSceneGraphNode] = {}

        for assertion in _latest_source_assertions(cognitive_map.state_assertions):
            state_key = _state_key(assertion.entity_id, assertion.attribute)
            node_id = f"state:{state_key}"
            node = node_index.get(node_id)
            if node is None:
                node = SemanticSceneGraphNode(
                    node_id=node_id,
                    kind="state",
                    label=state_key.replace(".", " "),
                    sources=[],
                    attributes={"state_key": state_key},
                    source_values={},
                    confidence=assertion.confidence,
                )
                node_index[node_id] = node
                graph.nodes.append(node)
            source = assertion.source.upper()
            if source not in node.sources:
                node.sources.append(source)
            node.source_values[source] = assertion.value
            node.confidence = min(node.confidence, assertion.confidence)

        for affordance in cognitive_map.runtime_affordances.values():
            self._add_affordance(graph, node_index, affordance)
        return graph

    def snapshot(self, graph: SemanticSceneGraph) -> dict[str, object]:
        from dataclasses import asdict

        return {
            "task_id": graph.task_id,
            "nodes": [asdict(node) for node in graph.nodes],
            "edges": [asdict(edge) for edge in graph.edges],
        }

    def _add_affordance(
        self,
        graph: SemanticSceneGraph,
        node_index: dict[str, SemanticSceneGraphNode],
        affordance: RuntimeAffordance,
    ) -> None:
        node_id = f"affordance:{affordance.id}"
        if node_id in node_index:
            return
        label = str(affordance.grounding.get("label") or affordance.action_name)
        node = SemanticSceneGraphNode(
            node_id=node_id,
            kind=affordance.action_type,
            label=label,
            sources=[affordance.source.upper()],
            attributes={
                "action": affordance.action_name,
                "grounding": dict(affordance.grounding),
                "entity_id": affordance.entity_id,
                "safety_level": affordance.grounding.get("safety_level", "low"),
            },
            source_values={"confidence": affordance.confidence},
            confidence=affordance.confidence,
        )
        node_index[node_id] = node
        graph.nodes.append(node)

        for target in _declared_state_targets(affordance):
            target_id = f"state:{target}"
            if target_id in node_index:
                graph.edges.append(
                    SemanticSceneGraphEdge(
                        source_id=node_id,
                        relation="grounds",
                        target_id=target_id,
                        confidence=affordance.confidence,
                    )
                )


# Backward-compatible import name.  This is a view builder, not another map.
CognitiveMapBuilder = SemanticSceneGraphViewBuilder


def _state_key(entity_id: str, attribute: str) -> str:
    return f"{canonical_state_name(entity_id)}.{canonical_state_name(attribute)}"


def _latest_source_assertions(assertions: list[StateAssertion]) -> list[StateAssertion]:
    latest: dict[tuple[str, str, str], StateAssertion] = {}
    for assertion in assertions:
        key = (assertion.entity_id, canonical_state_name(assertion.attribute), assertion.source)
        current = latest.get(key)
        if current is None or assertion.timestamp_ms >= current.timestamp_ms:
            latest[key] = assertion
    return list(latest.values())


def _declared_state_targets(affordance: RuntimeAffordance) -> list[str]:
    values: list[str] = []
    for key in ("observes", "effects", "achieves"):
        value = affordance.grounding.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return [".".join(canonical_state_name(part) for part in target.split(".")) for target in values]
