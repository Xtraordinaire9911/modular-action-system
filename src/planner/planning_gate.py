"""Planner view and System-2 packaging over the canonical runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.contracts.types import (
    Affordance,
    ArbiterDecision,
    Observation,
    SemanticSceneGraph,
    SkillCall,
    System2RecoveryRequest,
)
from src.planner.cognitive_map import SemanticSceneGraphViewBuilder
from src.planner.epistemic_arbiter import planner_decision_from_fusion
from src.planner.system2_recovery import System2RecoveryPlanner
from src.runtime.cognitive_map import CognitiveMap
from src.verification.conflict_detector import EpistemicArbiter, FusionDecision


@dataclass
class PlanningGateResult:
    cognitive_map: CognitiveMap
    scene_graph: SemanticSceneGraph
    fusion_decision: FusionDecision
    decision: ArbiterDecision
    recovery_request: System2RecoveryRequest | None


class PlanningGate:
    """Expose planner contracts without creating a second state or arbiter."""

    def __init__(
        self,
        map_builder: SemanticSceneGraphViewBuilder | None = None,
        arbiter: EpistemicArbiter | None = None,
        recovery_planner: System2RecoveryPlanner | None = None,
        grounding_confidence_threshold: float = 0.9,
    ) -> None:
        self.map_builder = map_builder or SemanticSceneGraphViewBuilder()
        self.arbiter = arbiter or EpistemicArbiter()
        self.recovery_planner = recovery_planner or System2RecoveryPlanner()
        self.grounding_confidence_threshold = grounding_confidence_threshold

    def evaluate(
        self,
        observation: Observation,
        affordances: Iterable[Affordance] = (),
        skill_call: SkillCall | None = None,
        task_id: str = "task",
    ) -> PlanningGateResult:
        cognitive_map = self.map_builder.build_runtime_map(observation, affordances, task_id)
        graph = self.map_builder.build_from_map(cognitive_map)
        fusion = self.arbiter.fuse(cognitive_map)
        decision = planner_decision_from_fusion(fusion)
        if decision.allow_system1 and any(
            affordance.confidence < self.grounding_confidence_threshold
            for affordance in cognitive_map.runtime_affordances.values()
        ):
            decision = ArbiterDecision(
                allow_system1=False,
                reason="grounding confidence below threshold; active perception required",
                conflicts=[],
                recommended_probe="reroute_backend",
            )
        recovery_request = None
        if not decision.allow_system1:
            recovery_request = self.recovery_planner.build_request(
                graph=graph,
                decision=decision,
                failed_skill=skill_call,
            )
        return PlanningGateResult(
            cognitive_map=cognitive_map,
            scene_graph=graph,
            fusion_decision=fusion,
            decision=decision,
            recovery_request=recovery_request,
        )
