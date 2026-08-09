"""Probe-based diagnosis must separate failures that look identical from outside.

An occluded button and a disabled one are both "present, correct place, click
did nothing". Only a measurement tells them apart, and they need different
responses, so this is where the reasoning has to earn its keep.
"""

from __future__ import annotations

from src.demos.diagnosis import (
    CAUSE_EXPLANATION,
    CAUSE_INERT,
    CAUSE_MOVED,
    CAUSE_OBSCURED,
    CAUSE_OCCLUDED,
    CAUSE_UNKNOWN,
    CAUSE_VANISHED,
    STRATEGY_CLEAR,
    STRATEGY_ESCALATE,
    STRATEGY_REROUTE,
    STRATEGY_RETRY,
    STRATEGY_ROLLBACK,
    diagnose_with_probes,
)
from src.demos.probes import HitTest, Interactability, Observation, Occlusion


def _observation(*, covered=False, exists=True, actionable=True, changed=False, landed=True) -> Observation:
    """``landed`` is what the hit test saw: did the click reach the intended element."""
    return Observation(
        hit=HitTest(hit_tag="div" if covered else "button", is_target=landed and not covered, ok=True),
        interact=Interactability(
            exists=exists,
            disabled=not actionable,
            visible=True,
            ok=True,
        ),
        occlusion=Occlusion(covered=covered, coverer_tag="div", coverer_text="Accept all", coverer_z="9000", ok=True),
        text_before="before",
        text_after="after" if changed else "before",
    )


# --- the cases that look the same from outside ---------------------------------


def test_an_occluded_control_is_not_confused_with_a_broken_one():
    """Present, enabled, unmoved - and the click never reached it."""
    result = diagnose_with_probes(_observation(covered=True), moved=False, still_present=True)

    assert result.cause == CAUSE_OCCLUDED
    assert result.strategy == STRATEGY_CLEAR and result.tier == 2
    assert "Accept all" in result.alternative_label


def test_a_disabled_control_is_not_confused_with_an_occluded_one():
    result = diagnose_with_probes(_observation(actionable=False), moved=False, still_present=True)

    assert result.cause == CAUSE_OBSCURED
    assert result.strategy == STRATEGY_ROLLBACK and result.tier == 3


def test_an_accepted_action_that_changed_nothing_escalates():
    result = diagnose_with_probes(_observation(changed=False), moved=False, still_present=True)

    assert result.cause == CAUSE_INERT
    assert result.strategy == STRATEGY_ESCALATE and result.tier == 4


def test_occlusion_is_checked_before_anything_else():
    """A covered target could also look unchanged; the hit test decides."""
    both = _observation(covered=True, changed=False)
    assert diagnose_with_probes(both, moved=False, still_present=True).cause == CAUSE_OCCLUDED


# --- the simpler cases still work ------------------------------------------------


def test_a_moved_control_the_click_missed_is_a_retry():
    result = diagnose_with_probes(_observation(landed=False), moved=True, still_present=True)
    assert result.cause == CAUSE_MOVED and result.strategy == STRATEGY_RETRY and result.tier == 1


def test_a_control_that_moved_after_receiving_the_click_is_not_a_retry():
    """Moving is only an explanation when the click missed; here it was delivered."""
    result = diagnose_with_probes(_observation(changed=True, landed=True), moved=True, still_present=True)

    assert result.cause == CAUSE_INERT and result.tier == 4


def test_a_vanished_control_with_a_route_is_a_reroute():
    result = diagnose_with_probes(
        _observation(exists=False), moved=False, still_present=False, alternative_label="Buy now"
    )
    assert result.cause == CAUSE_VANISHED and result.strategy == STRATEGY_REROUTE and result.tier == 2


def test_a_vanished_control_without_a_route_escalates():
    result = diagnose_with_probes(_observation(exists=False), moved=False, still_present=False)
    assert result.cause == CAUSE_VANISHED and result.tier == 4


def test_an_action_undone_a_moment_later_is_still_no_effect():
    """An optimistic rollback changes the region, just not to what the goal named."""
    result = diagnose_with_probes(_observation(changed=True), moved=False, still_present=True)

    assert result.cause == CAUSE_INERT and result.tier == 4
    assert any("not to the state the goal named" in line for line in result.evidence)


def test_a_conclusion_is_refused_when_no_probe_could_run():
    """Every probe failed, so any conclusion would be invented rather than measured."""
    blind = Observation(hit=HitTest(), interact=Interactability(), occlusion=Occlusion())
    result = diagnose_with_probes(blind, moved=False, still_present=True)

    assert result.cause == CAUSE_UNKNOWN
    assert result.tier == 4 and result.confidence < 0.5


# --- five distinct answers -------------------------------------------------------


def test_the_five_situations_produce_five_conclusions():
    conclusions = {
        diagnose_with_probes(_observation(covered=True), moved=False, still_present=True).cause,
        diagnose_with_probes(_observation(actionable=False), moved=False, still_present=True).cause,
        diagnose_with_probes(_observation(landed=False), moved=True, still_present=True).cause,
        diagnose_with_probes(_observation(exists=False), moved=False, still_present=False).cause,
        diagnose_with_probes(_observation(changed=False), moved=False, still_present=True).cause,
    }
    assert len(conclusions) == 5, f"expected five distinct causes, got {conclusions}"


# --- every conclusion is explained in words -------------------------------------


def test_every_cause_has_a_plain_language_account():
    for cause in (CAUSE_MOVED, CAUSE_OCCLUDED, CAUSE_OBSCURED, CAUSE_INERT, CAUSE_VANISHED, CAUSE_UNKNOWN):
        text = CAUSE_EXPLANATION[cause]
        assert len(text) > 60, f"{cause} needs a real explanation"


def test_the_explanation_travels_with_the_diagnosis():
    result = diagnose_with_probes(_observation(covered=True), moved=False, still_present=True)

    assert "receiving the click" in result.reasoning
    assert result.reasoning in result.explain()
    assert "what the agent measured" in result.explain()
