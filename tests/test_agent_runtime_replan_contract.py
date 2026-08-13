"""Invariant tests for the Agent/Planner <-> Runtime recovery boundary."""

import pytest

from src.runtime.action_context import FailureContext, build_action_context
from src.runtime.affordance_controller import AffordanceController
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance
from src.runtime.plan_validator import PlanValidator
from src.runtime.primitive_action import PrimitiveAction
from src.runtime.system2_planner import System2Planner


def _affordance(affordance_id: str, *, entity: str, grounding: dict, confidence: float = 0.9):
    grounding = {"safety_level": "low", **grounding}
    return RuntimeAffordance(
        id=affordance_id,
        source="dom",
        entity_id=entity,
        action_name="click",
        action_type="button",
        confidence=confidence,
        grounding=grounding,
    )


def _failure(target_id: str, target_entity: str = "target") -> FailureContext:
    return FailureContext(
        failed_action="click",
        failed_affordance_id=target_id,
        failed_entity_id=target_entity,
        expected_effect="oracle.done == true",
        failure_boundary="recoverable_execution_failure",
        failure_type="postcondition_failed",
        reason="fresh verification rejected the action",
        transition_id="episode-x:transition-0001",
        observation_state_id="state-fresh",
    )


@pytest.mark.parametrize(
    ("target_id", "repair_id", "label"),
    [
        ("target-a17", "control-q91", "Proceed"),
        ("goal-812", "dismiss-004", "Fortfahren"),
        ("action-random", "button-unseen", "继续"),
        ("x-19", "y-73", "Acknowledge"),
    ],
)
def test_replan_policy_is_invariant_to_ids_and_labels(target_id, repair_id, label):
    cognitive_map = CognitiveMap(task_id="generated-holdout")
    cognitive_map.add_affordance(_affordance(target_id, entity="target", grounding={"selector": "#private"}))
    cognitive_map.add_affordance(
        _affordance(
            repair_id,
            entity="obstruction",
            grounding={
                "selector": ".private-backend-handle",
                "label": label,
                "recovery_role": "clear_obstruction",
                "remediates": target_id,
                "recovery_postcondition": "obstruction.present == false",
                "recovery_safe": True,
                "idempotent": True,
                "irreversible": False,
            },
        )
    )

    context = build_action_context(
        cognitive_map,
        request_type="goal_spec",
        failure=_failure(target_id),
        remaining_steps=4,
        remaining_retries=0,
    )
    plan = System2Planner(AffordanceController()).plan(context, goal_id="goal", goal_state="oracle.done == true")

    assert plan.actions == [
        PrimitiveAction("click", affordance_id=repair_id, expected_effect="obstruction.present == false")
    ]
    assert "selector" not in context.affordances[0].grounding
    assert "selector" not in context.affordances[1].grounding


def test_ambiguous_recovery_candidates_fail_closed():
    cognitive_map = CognitiveMap(task_id="ambiguous")
    cognitive_map.add_affordance(_affordance("target", entity="target", grounding={}))
    for repair_id in ("repair-a", "repair-b"):
        cognitive_map.add_affordance(
            _affordance(
                repair_id,
                entity="obstruction",
                grounding={
                    "recovery_role": "clear_obstruction",
                    "remediates": "target",
                    "recovery_postcondition": "obstruction.present == false",
                    "recovery_safe": True,
                    "idempotent": True,
                    "irreversible": False,
                },
            )
        )

    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure("target"))
    plan = System2Planner(AffordanceController()).plan(context, goal_id="goal", goal_state="oracle.done == true")

    assert plan.requires_escalation
    assert plan.actions[0].action == "ask_user"


def test_runtime_validator_rejects_unobserved_replan_proposal():
    cognitive_map = CognitiveMap(task_id="stale-proposal")
    cognitive_map.add_affordance(_affordance("observed", entity="target", grounding={}))
    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure("observed"))

    validation = PlanValidator().validate(
        context,
        [PrimitiveAction("click", affordance_id="not-in-fresh-observation")],
    )

    assert not validation.valid
    assert validation.errors == ["unknown affordance_id: not-in-fresh-observation"]


def test_missing_recovery_capability_has_one_deterministic_fail_closed_plan():
    cognitive_map = CognitiveMap(task_id="unsupported")
    cognitive_map.add_affordance(_affordance("failed", entity="target", grounding={}))
    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure("failed"))
    planner = System2Planner(AffordanceController())

    first = planner.plan(context, goal_id="goal", goal_state="oracle.done == true")
    second = planner.plan(context, goal_id="goal", goal_state="oracle.done == true")

    assert first == second
    assert first.requires_escalation
    assert (
        first.reason == "failure requires semantic replanning but no safe, verifiable recovery affordance was observed"
    )


@pytest.mark.parametrize("relation", ["remediates", "compensates", "equivalent_to", "restores", "observes"])
def test_same_planner_selects_every_generic_recovery_relation(relation):
    target = "failed-target"
    cognitive_map = CognitiveMap(task_id=f"relation-{relation}")
    cognitive_map.add_affordance(_affordance(target, entity="failed-entity", grounding={}))
    relation_target = "episode-x:transition-0001" if relation == "compensates" else target
    cognitive_map.add_affordance(
        _affordance(
            "fresh-capability",
            entity="recovery-capability",
            grounding={
                relation: relation_target,
                "recovery_postcondition": "oracle.capability_effect == true",
                "recovery_safe": True,
                "idempotent": True,
                "irreversible": False,
            },
        )
    )

    context = build_action_context(cognitive_map, request_type="goal_spec", failure=_failure(target))
    plan = System2Planner(AffordanceController()).plan(context, goal_id="goal", goal_state="oracle.done == true")

    assert not plan.requires_escalation
    assert plan.actions == [
        PrimitiveAction(
            "click",
            affordance_id="fresh-capability",
            expected_effect="oracle.capability_effect == true",
        )
    ]
