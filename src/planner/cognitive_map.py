"""Member A cognitive map construction.

This module builds a Semantic Scene Graph from the typed Observation contract
and optional Affordance objects produced perception modules. It is
deliberately deterministic: System 1 should only act on structured state, not
on raw HTML or raw TD blobs.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any, Iterable

from src.contracts.types import (
    Affordance,
    Observation,
    SemanticSceneGraph,
    SemanticSceneGraphEdge,
    SemanticSceneGraphNode,
)


class CognitiveMapBuilder:
    """Fuse DOM, visual, and WoT observations into a Semantic Scene Graph."""

    def build(
        self,
        observation: Observation,
        affordances: Iterable[Affordance] = (),
        task_id: str = "task",
    ) -> SemanticSceneGraph:
        graph = SemanticSceneGraph(task_id=task_id)
        node_index: dict[str, SemanticSceneGraphNode] = {}

        self._add_observed_state(graph, node_index, "WOT", observation.device_states)

        tree = observation.accessibility_tree or {}
        self._add_observed_state(graph, node_index, "DOM", tree.get("page_state", {}))
        self._add_observed_state(graph, node_index, "VISUAL", tree.get("visual_state", {}))

        for affordance in affordances:
            self._add_affordance(graph, node_index, affordance)

        return graph

    def snapshot(self, graph: SemanticSceneGraph) -> dict[str, Any]:
        return {
            "task_id": graph.task_id,
            "nodes": [asdict(node) for node in graph.nodes],
            "edges": [asdict(edge) for edge in graph.edges],
        }

    def _add_observed_state(
        self,
        graph: SemanticSceneGraph,
        node_index: dict[str, SemanticSceneGraphNode],
        source: str,
        state: dict[str, Any],
    ) -> None:
        for path, value in _walk_leaves(state):
            state_key = _canonical_state_key(path)
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
                    confidence=1.0,
                )
                node_index[node_id] = node
                graph.nodes.append(node)
            if source not in node.sources:
                node.sources.append(source)
            node.source_values[source] = value

    def _add_affordance(
        self,
        graph: SemanticSceneGraph,
        node_index: dict[str, SemanticSceneGraphNode],
        affordance: Affordance,
    ) -> None:
        node_id = f"affordance:{affordance.id}"
        if node_id in node_index:
            return
        node = SemanticSceneGraphNode(
            node_id=node_id,
            kind=affordance.type,
            label=affordance.label,
            sources=[affordance.source],
            attributes={
                "action": affordance.action,
                "locator": affordance.locator,
                "state": affordance.state,
                "safety_level": affordance.safety_level,
            },
            source_values={"confidence": affordance.confidence},
            confidence=affordance.confidence,
        )
        node_index[node_id] = node
        graph.nodes.append(node)

        target_key = _infer_state_target(affordance)
        if target_key:
            graph.edges.append(
                SemanticSceneGraphEdge(
                    source_id=node_id,
                    relation="grounds",
                    target_id=f"state:{target_key}",
                    confidence=affordance.confidence,
                )
            )


def _walk_leaves(data: dict[str, Any], prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            yield from _walk_leaves(value, path)
        else:
            yield path, value


def _canonical_state_key(path: str) -> str:
    parts = [_camel_to_snake(part).replace("-", "_").lower() for part in path.split(".")]
    return ".".join(parts)


def _camel_to_snake(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value


def _infer_state_target(affordance: Affordance) -> str | None:
    label = _canonical_state_key(affordance.label)
    thing_id = str(affordance.locator.get("thing_id", "")).lower()
    if "temperature" in label or "thermostat" in thing_id:
        return "thermostat.target_temperature"
    if "brightness" in label or "light" in thing_id:
        return "lighting.brightness"
    if "power" in label or "projector" in thing_id:
        return "projector.power"
    if "booking" in label or "book" in label:
        return "booking.status"
    return None
