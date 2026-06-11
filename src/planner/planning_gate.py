"""Single entry point for Member A's pre-execution decision logic."""

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
from src.planner.cognitive_map import CognitiveMapBuilder
from src.planner.epistemic_arbiter import EpistemicArbiter
from src.planner.system2_recovery import System2RecoveryPlanner


@dataclass
class PlanningGateResult:
    scene_graph: SemanticSceneGraph
    decision: ArbiterDecision
    recovery_request: System2RecoveryRequest | None


class PlanningGate:
    """Compose cognitive-map construction, arbitration, and System 2 packaging."""

    def __init__(
        self,
        map_builder: CognitiveMapBuilder | None = None,
        arbiter: EpistemicArbiter | None = None,
        recovery_planner: System2RecoveryPlanner | None = None,
    ) -> None:
        self.map_builder = map_builder or CognitiveMapBuilder()
        self.arbiter = arbiter or EpistemicArbiter()
        self.recovery_planner = recovery_planner or System2RecoveryPlanner()

    def evaluate(
        self,
        observation: Observation,
        affordances: Iterable[Affordance] = (),
        skill_call: SkillCall | None = None,
        task_id: str = "task",
    ) -> PlanningGateResult:
        scene_graph = self.map_builder.build(observation=observation, affordances=affordances, task_id=task_id)
        decision = self.arbiter.decide(scene_graph)
        recovery_request = None
        if not decision.allow_system1:
            recovery_request = self.recovery_planner.build_request(
                graph=scene_graph,
                decision=decision,
                failed_skill=skill_call,
            )
        return PlanningGateResult(
            scene_graph=scene_graph,
            decision=decision,
            recovery_request=recovery_request,
        )
