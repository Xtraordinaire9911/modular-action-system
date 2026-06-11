"""Tests for the cost-aware backend router and confidence tracker (Member B)."""

from __future__ import annotations

from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.backend_router.router import CostAwareRouter
from src.contracts.types import Affordance, SkillTuple


def _skill(allowed=("dom", "wot", "visual"), preferred=()):
    return SkillTuple(
        skill_id="set_temperature",
        description="",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=list(allowed),
        preferred_backends=list(preferred),
        rollback=None,
        failure_modes={},
        timeout_ms=2000,
        safety_level="low",
        irreversible=False,
    )


def _cands():
    return {
        "wot": Affordance("wot_x", "WOT", "action", "x", "invoke", {"href": "h"}, 1.0),
        "dom": Affordance("dom_x", "DOM", "input", "x", "type", {"selector": "#x"}, 0.9),
        "visual": Affordance("vis_x", "VISUAL", "button", "x", "click", {"mark_id": "M0"}, 0.95),
    }


def test_router_prefers_cheap_reliable_wot_when_all_equal():
    decision = CostAwareRouter().route(_skill(), _cands())
    assert decision.selected_backend == "wot"  # cheapest cost proxy, equal reliability
    assert decision.candidate_backends[0] == "wot"


def test_router_never_picks_visual_over_healthy_deterministic():
    decision = CostAwareRouter().route(_skill(), _cands())
    assert decision.selected_backend != "visual"


def test_mode_gating_restricts_backends():
    decision = CostAwareRouter(mode="dom-only").route(_skill(), _cands())
    assert decision.selected_backend == "dom"
    decision_v = CostAwareRouter(mode="vam-only").route(_skill(), _cands())
    assert decision_v.selected_backend == "visual"


def test_unavailable_backend_yields_no_route():
    decision = CostAwareRouter(mode="wot-only").route(_skill(allowed=("dom",)), _cands())
    assert decision.selected_backend is None and decision.score == float("inf")


def test_reliability_drop_reroutes_away_from_failing_backend():
    tracker = BackendConfidenceTracker(alpha=0.6)
    router = CostAwareRouter(tracker)
    for _ in range(5):  # WoT keeps timing out
        router.observe("wot", success=False, latency_ms=2000)
    decision = router.route(_skill(), _cands())
    assert decision.selected_backend != "wot"  # learned unreliability pushes it down


def test_preferred_backend_breaks_ties():
    # equal cost/reliability for two synthetic backends → preference decides
    tracker = BackendConfidenceTracker()
    router = CostAwareRouter(tracker, cost={"dom": 0.3, "wot": 0.3, "visual": 1.0})
    decision = router.route(_skill(preferred=("dom",)), _cands())
    assert decision.selected_backend == "dom"
