"""Regression tests for the canonical map, arbiter, and router architecture."""

from src.backend_router.router import CostAwareRouter
from src.contracts.types import Affordance, Observation, SkillTuple
from src.planner import CognitiveMapBuilder, PlanningGate, SemanticSceneGraphViewBuilder
from src.planner import epistemic_arbiter as planner_arbiter
from src.runtime.backend_router import RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap, StateAssertion
from src.verification.conflict_detector import EpistemicArbiter


def _skill() -> SkillTuple:
    return SkillTuple(
        skill_id="set_temperature",
        description="Set a target temperature",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["dom", "wot"],
        preferred_backends=["wot"],
        rollback=None,
        failure_modes={},
        timeout_ms=2000,
        safety_level="low",
        irreversible=False,
    )


def _candidates() -> dict[str, Affordance]:
    return {
        "wot": Affordance(
            "wot_temperature",
            "WOT",
            "action",
            "Set temperature",
            "invoke",
            {"thing_id": "thermostat"},
            1.0,
        ),
        "dom": Affordance(
            "dom_temperature",
            "DOM",
            "input",
            "Target temperature",
            "type",
            {"entity_id": "thermostat"},
            0.9,
        ),
    }


def test_planning_gate_uses_runtime_map_and_canonical_arbiter():
    gate = PlanningGate()
    observation = Observation(
        device_states={"thermostat": {"targetTemperature": 24}},
        accessibility_tree={"page_state": {"thermostat": {"target_temperature": 20}}},
    )

    result = gate.evaluate(observation, task_id="unified_conflict")

    assert isinstance(result.cognitive_map, CognitiveMap)
    assert isinstance(gate.arbiter, EpistemicArbiter)
    assert result.fusion_decision.allow_system1 is False
    assert result.decision.allow_system1 is False
    assert result.decision.conflicts[0].state_key == "thermostat.target_temperature"


def test_planner_compatibility_names_do_not_create_second_core_implementations():
    assert CognitiveMapBuilder is SemanticSceneGraphViewBuilder
    assert planner_arbiter.EpistemicArbiter is EpistemicArbiter


def test_scene_graph_is_a_read_only_view_of_runtime_state():
    cognitive_map = CognitiveMap(task_id="view")
    cognitive_map.add_state_assertion(StateAssertion("lights", "brightness", 40, "wot", confidence=0.9))
    cognitive_map.add_state_assertion(StateAssertion("lights", "brightness", 40, "dom", confidence=0.8))

    graph = SemanticSceneGraphViewBuilder().build_from_map(cognitive_map)

    state_node = next(node for node in graph.nodes if node.node_id == "state:lights.brightness")
    assert state_node.source_values == {"WOT": 40, "DOM": 40}
    assert state_node.confidence == 0.8
    assert cognitive_map.get_latest_state("lights", "brightness", source="wot") is not None


def test_scene_graph_uses_latest_timestamp_per_source_not_append_order():
    cognitive_map = CognitiveMap(task_id="history")
    cognitive_map.add_state_assertion(
        StateAssertion("lights", "brightness", 70, "wot", confidence=0.9, timestamp_ms=20)
    )
    cognitive_map.add_state_assertion(
        StateAssertion("lights", "brightness", 10, "wot", confidence=0.2, timestamp_ms=10)
    )

    graph = SemanticSceneGraphViewBuilder().build_from_map(cognitive_map)

    state_node = next(node for node in graph.nodes if node.node_id == "state:lights.brightness")
    assert state_node.source_values == {"WOT": 70}
    assert state_node.confidence == 0.9


def test_planning_gate_keeps_grounding_confidence_as_a_separate_gate():
    low_confidence = Affordance(
        "dom_submit",
        "DOM",
        "button",
        "Submit",
        "click",
        {"entity_id": "form"},
        0.4,
    )

    result = PlanningGate().evaluate(Observation(), [low_confidence], task_id="low_grounding")

    assert result.fusion_decision.allow_system1 is True
    assert result.decision.allow_system1 is False
    assert result.decision.recommended_probe == "reroute_backend"


def test_legacy_cost_router_delegates_to_canonical_runtime_router():
    router = CostAwareRouter()

    assert isinstance(router.core, RuntimeBackendRouter)
    assert router.route(_skill(), _candidates()).backend == "wot"

    for _ in range(5):
        router.observe("wot", success=False, latency_ms=2000)

    assert router.route(_skill(), _candidates()).backend == "dom"
