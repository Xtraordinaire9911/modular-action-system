"""Compatibility conversion from a planner graph to canonical runtime fusion.

There is intentionally no second ``EpistemicArbiter`` class here.  Planner
callers use the runtime arbiter and this module only translates its decision to
the older planner-facing contract when needed.
"""

from __future__ import annotations

from src.contracts.types import ArbiterDecision, SemanticSceneGraph, SensoryConflict
from src.runtime.cognitive_map import CognitiveMap, StateAssertion
from src.verification.conflict_detector import EpistemicArbiter, FusionDecision


def decide_scene_graph(
    graph: SemanticSceneGraph,
    *,
    arbiter: EpistemicArbiter | None = None,
    confidence_threshold: float = 0.9,
) -> tuple[ArbiterDecision, FusionDecision]:
    """Evaluate a legacy graph through the canonical runtime arbiter."""

    cognitive_map = _map_from_graph(graph)
    fusion = (arbiter or EpistemicArbiter()).fuse(cognitive_map)
    decision = planner_decision_from_fusion(fusion)
    if decision.allow_system1:
        low_confidence = [
            node for node in graph.nodes if node.kind != "state" and node.confidence < confidence_threshold
        ]
        if low_confidence:
            decision = ArbiterDecision(
                allow_system1=False,
                reason="grounding confidence below threshold; active perception required",
                conflicts=[],
                recommended_probe="reroute_backend",
            )
    return decision, fusion


def planner_decision_from_fusion(fusion: FusionDecision) -> ArbiterDecision:
    conflicts = [
        SensoryConflict(
            conflict_id=conflict.id,
            node_id=f"state:{conflict.entity_id}.{conflict.attribute}",
            state_key=f"{conflict.entity_id}.{conflict.attribute}",
            sources=[source.upper() for source in conflict.sources],
            values={source.upper(): value for source, value in conflict.values.items()},
            severity=conflict.severity,
            resolved=conflict.resolved,
            recommended_probe=_recommended_probe(conflict.sources),
        )
        for conflict in fusion.conflicts
    ]
    return ArbiterDecision(
        allow_system1=fusion.allow_system1,
        reason=fusion.reason,
        conflicts=conflicts,
        recommended_probe=_choose_probe(conflicts) if conflicts else None,
    )


def _map_from_graph(graph: SemanticSceneGraph) -> CognitiveMap:
    cognitive_map = CognitiveMap(task_id=graph.task_id)
    for node in graph.nodes:
        if node.kind != "state":
            continue
        state_key = str(node.attributes.get("state_key", node.node_id.removeprefix("state:")))
        entity_id, _, attribute = state_key.rpartition(".")
        for source, value in node.source_values.items():
            normalized_source = source.lower()
            if normalized_source not in {"dom", "wot", "visual", "system"}:
                continue
            cognitive_map.add_state_assertion(
                StateAssertion(
                    entity_id=entity_id or "state",
                    attribute=attribute or state_key,
                    value=value,
                    source=normalized_source,  # type: ignore[arg-type]
                    confidence=node.confidence,
                )
            )
    return cognitive_map


def _recommended_probe(sources: list[str]) -> str:
    normalized = {source.lower() for source in sources}
    if "wot" in normalized and ({"dom", "visual"} & normalized):
        return "repoll_sensor"
    if "dom" in normalized:
        return "refresh_page"
    return "reroute_backend"


def _choose_probe(conflicts: list[SensoryConflict]) -> str:
    if any(conflict.severity == "high" for conflict in conflicts):
        return "repoll_sensor"
    return conflicts[0].recommended_probe if conflicts else "reroute_backend"
