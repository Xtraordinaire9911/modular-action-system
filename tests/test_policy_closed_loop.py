"""Closed-loop adaptation: approved proposals change router behaviour next run.

These tests prove the final self-evolution step — an approved, low-risk,
release-gated proposal is applied to a persistent policy overlay, and the
runtime backend router then routes the affected skill differently. Refused
proposals, reverts, regression-safety (no overlay == old behaviour), and
persistence are all covered.
"""

from __future__ import annotations

from src.adaptation.policy_store import (
    PolicyOverlay,
    PolicyStore,
    apply_proposal,
    revert_proposal,
)
from src.contracts.types import SkillCall
from src.runtime.backend_router import RuntimeBackendRouter
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance


def _map_with_wot_and_dom(skill: str = "set_temperature") -> CognitiveMap:
    cognitive_map = CognitiveMap(task_id="t")
    cognitive_map.add_affordance(
        RuntimeAffordance(
            id="wot_set", source="wot", entity_id="thermostat", action_name="set",
            action_type="action", confidence=0.9, grounding={}, skill_names=[skill],
        )
    )
    cognitive_map.add_affordance(
        RuntimeAffordance(
            id="dom_set", source="dom", entity_id="thermostat", action_name="set",
            action_type="input", confidence=0.9, grounding={}, skill_names=[skill],
        )
    )
    return cognitive_map


def _approved_reliability_proposal(pid: str = "policy_001") -> dict:
    return {
        "proposal_id": pid,
        "change_type": "backend_reliability_adjustment",
        "signature": "set_temperature|wot|timeout|smart_room:thermostat",
        "release_gate": {"approved": True, "safe_to_apply": True},
    }


def _call() -> SkillCall:
    return SkillCall(skill_id="set_temperature", params={})


def test_router_without_overlay_keeps_preferred_wot():
    # Regression guard: default construction is unchanged.
    decision = RuntimeBackendRouter().select_backend(_call(), _map_with_wot_and_dom())
    assert decision.backend == "wot"


def test_approved_proposal_reroutes_skill_next_run():
    overlay = PolicyOverlay()
    outcome = apply_proposal(overlay, _approved_reliability_proposal())
    assert outcome.applied
    decision = RuntimeBackendRouter(policy_overlay=overlay).select_backend(_call(), _map_with_wot_and_dom())
    assert decision.backend == "dom"  # learned penalty on wot flips the choice


def test_unapproved_proposal_is_refused():
    overlay = PolicyOverlay()
    proposal = _approved_reliability_proposal()
    proposal["release_gate"] = {"approved": False, "safe_to_apply": False}
    outcome = apply_proposal(overlay, proposal)
    assert not outcome.applied and "not approved" in outcome.reason
    assert RuntimeBackendRouter(policy_overlay=overlay).select_backend(_call(), _map_with_wot_and_dom()).backend == "wot"


def test_forbidden_change_type_is_refused():
    overlay = PolicyOverlay()
    proposal = _approved_reliability_proposal()
    proposal["change_type"] = "model_weight_change"
    outcome = apply_proposal(overlay, proposal)
    assert not outcome.applied and "forbidden" in outcome.reason


def test_double_apply_is_idempotent():
    overlay = PolicyOverlay()
    assert apply_proposal(overlay, _approved_reliability_proposal()).applied
    second = apply_proposal(overlay, _approved_reliability_proposal())
    assert not second.applied and "already applied" in second.reason


def test_revert_restores_original_routing():
    overlay = PolicyOverlay()
    apply_proposal(overlay, _approved_reliability_proposal())
    assert revert_proposal(overlay, "policy_001")
    decision = RuntimeBackendRouter(policy_overlay=overlay).select_backend(_call(), _map_with_wot_and_dom())
    assert decision.backend == "wot"


def test_overlay_persists_across_runs(tmp_path):
    store = PolicyStore(tmp_path / "adaptation_policy.json")
    overlay = store.load()
    apply_proposal(overlay, _approved_reliability_proposal())
    store.save(overlay)

    # A fresh process would reload the overlay from disk and route accordingly.
    reloaded = PolicyStore(tmp_path / "adaptation_policy.json").load()
    decision = RuntimeBackendRouter(policy_overlay=reloaded).select_backend(_call(), _map_with_wot_and_dom())
    assert decision.backend == "dom"


def test_preferred_backend_override_applies():
    overlay = PolicyOverlay()
    proposal = {
        "proposal_id": "policy_pref",
        "change_type": "preferred_backend_order_change",
        "signature": "set_temperature|dom||",
        "new_preferred_backend": "dom",
        "release_gate": {"approved": True, "safe_to_apply": True},
    }
    assert apply_proposal(overlay, proposal).applied
    decision = RuntimeBackendRouter(policy_overlay=overlay).select_backend(_call(), _map_with_wot_and_dom())
    assert decision.backend == "dom"
