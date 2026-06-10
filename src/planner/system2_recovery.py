"""System 2 recovery request construction.

Member A does not need to call an LLM directly here. The important contract is
to package conflicts, the scene graph, and allowed probing actions so the VAM
or LLM supervisor is only invoked after System 1 has failed or been blocked.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.contracts.types import (
    ArbiterDecision,
    SemanticSceneGraph,
    SensoryConflict,
    SkillCall,
    System2RecoveryRequest,
)

DEFAULT_PROBES = ["refresh_page", "repoll_sensor", "reroute_backend", "escalate_human"]


class System2RecoveryPlanner:
    """Build deterministic recovery requests for a supervisor model."""

    def build_request(
        self,
        graph: SemanticSceneGraph,
        decision: ArbiterDecision,
        failed_skill: SkillCall | None = None,
        failure_reason: str | None = None,
    ) -> System2RecoveryRequest:
        reason = failure_reason or decision.reason
        conflicts = list(decision.conflicts)
        snapshot = _snapshot_graph(graph)
        allowed_probes = _allowed_probes(conflicts, decision.recommended_probe)
        prompt = format_system2_prompt(
            failed_skill=failed_skill,
            reason=reason,
            conflicts=conflicts,
            scene_graph_snapshot=snapshot,
            allowed_probes=allowed_probes,
        )
        return System2RecoveryRequest(
            failed_skill=failed_skill,
            reason=reason,
            conflicts=conflicts,
            scene_graph_snapshot=snapshot,
            allowed_probes=allowed_probes,
            prompt=prompt,
        )


def format_system2_prompt(
    failed_skill: SkillCall | None,
    reason: str,
    conflicts: list[SensoryConflict],
    scene_graph_snapshot: dict[str, Any],
    allowed_probes: list[str],
) -> str:
    skill_text = failed_skill.skill_id if failed_skill else "none"
    conflict_lines = _format_conflicts(conflicts)
    return (
        "System 1 failed or was blocked. Do not guess.\n"
        f"Failed skill: {skill_text}\n"
        f"Reason: {reason}\n"
        f"Allowed probing actions: {', '.join(allowed_probes)}\n"
        "Conflicts:\n"
        f"{conflict_lines}\n"
        "Scene graph snapshot:\n"
        f"{scene_graph_snapshot}\n"
        "Choose exactly one next action: refresh_page, repoll_sensor, reroute_backend, or escalate_human. "
        "If visual grounding is needed, select a grounded mark or affordance ID; never output raw coordinates."
    )


def _format_conflicts(conflicts: list[SensoryConflict]) -> str:
    if not conflicts:
        return "- none"
    lines = []
    for conflict in conflicts:
        lines.append(
            f"- {conflict.state_key}: values={conflict.values}, severity={conflict.severity}, "
            f"recommended_probe={conflict.recommended_probe}"
        )
    return "\n".join(lines)


def _snapshot_graph(graph: SemanticSceneGraph) -> dict[str, Any]:
    return {
        "task_id": graph.task_id,
        "nodes": [asdict(node) for node in graph.nodes],
        "edges": [asdict(edge) for edge in graph.edges],
    }


def _allowed_probes(conflicts: list[SensoryConflict], recommended_probe: str | None) -> list[str]:
    probes = list(DEFAULT_PROBES)
    if recommended_probe and recommended_probe in probes:
        probes.remove(recommended_probe)
        probes.insert(0, recommended_probe)
    if any(conflict.severity == "high" for conflict in conflicts) and "escalate_human" not in probes:
        probes.append("escalate_human")
    return probes
