"""Epistemic arbiter for Member A.

The arbiter is the gate between structured perception and fast System 1
execution. If the web UI, visual grounding, and WoT/device state disagree, it
blocks automatic execution and asks System 2 to resolve uncertainty first.
"""

from __future__ import annotations

from typing import Any, Literal

from src.contracts.types import (
    ArbiterDecision,
    SemanticSceneGraph,
    SemanticSceneGraphNode,
    SensoryConflict,
    SensoryConflictError,
)

Severity = Literal["low", "medium", "high"]


class EpistemicArbiter:
    """Compare source-specific facts in a Semantic Scene Graph."""

    def __init__(self, confidence_threshold: float = 0.9) -> None:
        self.confidence_threshold = confidence_threshold

    def decide(self, graph: SemanticSceneGraph) -> ArbiterDecision:
        conflicts = self.find_conflicts(graph)
        if conflicts:
            probe = _choose_probe(conflicts)
            return ArbiterDecision(
                allow_system1=False,
                reason="sensory conflict detected; System 1 execution is blocked",
                conflicts=conflicts,
                recommended_probe=probe,
            )

        low_confidence = self._low_confidence_nodes(graph)
        if low_confidence:
            return ArbiterDecision(
                allow_system1=False,
                reason="grounding confidence below threshold; active perception required",
                conflicts=[],
                recommended_probe="reroute_backend",
            )

        return ArbiterDecision(
            allow_system1=True,
            reason="all observed sources agree above confidence threshold",
            recommended_probe=None,
        )

    def find_conflicts(self, graph: SemanticSceneGraph) -> list[SensoryConflict]:
        conflicts: list[SensoryConflict] = []
        for node in graph.nodes:
            if node.kind != "state" or len(node.source_values) < 2:
                continue
            values = _distinct_values(node.source_values)
            if len(values) <= 1:
                continue
            state_key = str(node.attributes.get("state_key", node.node_id.removeprefix("state:")))
            conflicts.append(
                SensoryConflict(
                    conflict_id=f"conflict:{state_key}",
                    node_id=node.node_id,
                    state_key=state_key,
                    sources=list(node.source_values),
                    values=dict(node.source_values),
                    severity=_severity_for(state_key),
                    recommended_probe=_probe_for(node),
                )
            )
        return conflicts

    def raise_for_conflicts(self, decision: ArbiterDecision) -> None:
        if decision.allow_system1:
            return
        if decision.conflicts:
            conflict_keys = ", ".join(conflict.state_key for conflict in decision.conflicts)
            raise SensoryConflictError(f"System 1 blocked by sensory conflict: {conflict_keys}")

    def _low_confidence_nodes(self, graph: SemanticSceneGraph) -> list[SemanticSceneGraphNode]:
        return [node for node in graph.nodes if node.kind != "state" and node.confidence < self.confidence_threshold]


def _distinct_values(source_values: dict[str, Any]) -> set[tuple[str, str]]:
    return {_normalize_value(value) for value in source_values.values()}


def _normalize_value(value: Any) -> tuple[str, str]:
    if isinstance(value, bool):
        return ("bool", str(value).lower())
    if isinstance(value, (int, float)):
        return ("number", f"{float(value):.4f}")
    if value is None:
        return ("none", "")
    return ("text", str(value).strip().lower())


def _severity_for(state_key: str) -> Severity:
    high_risk_terms = ("occupancy", "ready", "readiness", "power", "booking")
    if any(term in state_key for term in high_risk_terms):
        return "high"
    if "temperature" in state_key or "brightness" in state_key:
        return "medium"
    return "low"


def _probe_for(node: SemanticSceneGraphNode) -> str:
    sources = set(node.source_values)
    if "WOT" in sources and ("DOM" in sources or "VISUAL" in sources):
        return "repoll_sensor"
    if "DOM" in sources:
        return "refresh_page"
    return "reroute_backend"


def _choose_probe(conflicts: list[SensoryConflict]) -> str:
    if any(conflict.severity == "high" for conflict in conflicts):
        return "repoll_sensor"
    return conflicts[0].recommended_probe if conflicts else "reroute_backend"
