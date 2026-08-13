"""Invariant tests for the Agent/Planner <-> Runtime recovery boundary."""

from src.runtime.action_context import FailureContext, build_action_context
from src.runtime.affordance_controller import AffordanceController, PrimitivePlan
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance
from src.runtime.plan_validator import PlanValidator
from src.runtime.planner_port import PlannerPort
from src.runtime.primitive_action import PrimitiveAction


def _affordance(affordance_id: str, grounding: dict) -> RuntimeAffordance:
    return RuntimeAffordance(
        id=affordance_id,
        source="dom",
        entity_id="entity",
        action_name="click",
        action_type="button",
        confidence=0.9,
        grounding={"safety_level": "low", **grounding},
    )


def _failure() -> FailureContext:
    return FailureContext(
        failed_action="click",
        failed_affordance_id="failed-target",
        failed_entity_id="entity",
        expected_effect="oracle.done == true",
        failure_boundary="recoverable_execution_failure",
        failure_type="postcondition_failed",
        reason="fresh verification rejected the action",
        transition_id="episode-x:transition-0001",
        observation_state_id="state-fresh",
        observation_request_id="request-fresh",
    )


def test_runtime_handoff_exposes_generic_capabilities_without_backend_selectors():
    cognitive_map = CognitiveMap("planner-port")
    cognitive_map.add_affordance(
        _affordance(
            "observed-capability",
            {
                "selector": "#private-runtime-handle",
                "compensates": "episode-x:transition-0001",
                "recovery_postcondition": "oracle.compensated == true",
                "recovery_safe": True,
                "idempotent": True,
                "irreversible": False,
            },
        )
    )

    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure())

    assert context.failure == _failure()
    assert context.affordances[0].grounding["compensates"] == "episode-x:transition-0001"
    assert "selector" not in context.affordances[0].grounding


def test_runtime_validator_rejects_unobserved_planner_proposal():
    cognitive_map = CognitiveMap("planner-validation")
    cognitive_map.add_affordance(_affordance("observed", {}))
    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure())

    validation = PlanValidator().validate(
        context,
        [PrimitiveAction("click", affordance_id="not-in-fresh-observation")],
    )

    assert not validation.valid
    assert validation.errors == ["unknown affordance_id: not-in-fresh-observation"]


def test_default_controller_does_not_implement_recovery_strategy():
    cognitive_map = CognitiveMap("no-borrowed-planner")
    cognitive_map.add_affordance(_affordance("failed-target", {}))
    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure())

    plan = AffordanceController().plan(context, goal_id="goal", goal_state="oracle.done == true")

    assert plan.requires_escalation
    assert plan.reason == "recovery planning is owned by the injected Agent/Planner implementation"


class _ExternalPlannerStub:
    def plan(self, context, *, goal_id="", goal_state="", parameters=None):
        _ = (context, goal_id, goal_state, parameters)
        return PrimitivePlan([PrimitiveAction("click", affordance_id="observed")])


def test_planner_port_accepts_an_externally_owned_implementation():
    planner: PlannerPort = _ExternalPlannerStub()

    assert (
        planner.plan(build_action_context(CognitiveMap("port"), request_type="goal_spec")).actions[0].action == "click"
    )
