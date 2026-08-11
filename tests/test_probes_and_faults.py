"""Probes must report what they measured, and faults must stay realistic.

A probe that returns a plausible default when it cannot run would make a
diagnosis look grounded while resting on nothing, so the tests here care most
about the failure paths.
"""

from __future__ import annotations

from typing import Any

from src.demos.probes import (
    HitTest,
    Observation,
    hit_test,
    interactability,
    occlusion,
    text_snapshot,
)
from src.demos.realistic_faults import FAULTS, difficulty_order


class FakeSession:
    def __init__(self, replies: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.replies = replies or {}
        self.fail = fail
        self.calls: list[str] = []

    def evaluate(self, expression: str, arg: Any = None) -> Any:
        if self.fail:
            raise RuntimeError("page closed")
        for token, reply in self.replies.items():
            if token in expression:
                self.calls.append(token)
                return reply
        return None


# --- probes report measurements, not verdicts ----------------------------------


def test_hit_test_reports_what_is_at_the_point():
    session = FakeSession({"elementFromPoint": {"tag": "div", "text": "We value your privacy", "is_target": False}})
    result = hit_test(session, 100, 200, "button")

    assert result.ok and not result.is_target
    assert "covered by <div>" in result.describe()
    assert "privacy" in result.describe()


def test_hit_test_confirms_when_the_aimed_element_is_hit():
    session = FakeSession({"elementFromPoint": {"tag": "button", "text": "Add", "is_target": True}})
    assert "is the one that was aimed at" in hit_test(session, 1, 1, "button").describe()


def test_a_probe_that_cannot_run_says_so_rather_than_guessing():
    """A default that looks like a finding would make the diagnosis dishonest."""
    broken = FakeSession(fail=True)

    assert hit_test(broken, 1, 1).ok is False
    assert "could not run" in hit_test(broken, 1, 1).describe()
    assert interactability(broken, "button").ok is False
    assert occlusion(broken, "button").ok is False
    assert text_snapshot(broken, "body") == ""


def test_interactability_names_every_reason_it_cannot_be_used():
    session = FakeSession(
        {
            "getBoundingClientRect": {
                "exists": True,
                "disabled": True,
                "aria_disabled": True,
                "visible": False,
                "pointer_events": "none",
            }
        }
    )
    result = interactability(session, "button")

    assert result.ok and not result.actionable
    text = result.describe()
    for reason in ("disabled attribute", "aria-disabled", "not rendered", "pointer-events"):
        assert reason in text


def test_interactability_reports_a_healthy_control():
    session = FakeSession(
        {
            "getBoundingClientRect": {
                "exists": True,
                "disabled": False,
                "aria_disabled": False,
                "visible": True,
                "pointer_events": "auto",
            }
        }
    )
    assert interactability(session, "button").actionable
    assert "accepts input" in interactability(session, "button").describe()


def test_missing_element_is_distinguished_from_a_disabled_one():
    session = FakeSession({"getBoundingClientRect": {"exists": False}})
    assert "no longer in the document" in interactability(session, "button").describe()


def test_occlusion_names_what_is_covering_the_target():
    session = FakeSession({"elementFromPoint": {"covered": True, "tag": "div", "text": "Accept all", "z": "9000"}})
    result = occlusion(session, "button")

    assert result.covered
    assert "Accept all" in result.describe() and "9000" in result.describe()


# --- the combined observation ---------------------------------------------------


def test_observation_turns_measurements_into_readable_evidence():
    observation = Observation(
        hit=HitTest(hit_tag="div", hit_text="We value your privacy", is_target=False, ok=True),
        text_before="Cart is empty",
        text_after="Cart is empty",
    )
    evidence = observation.evidence()

    assert len(evidence) == 4
    assert any("covered by" in line for line in evidence)
    assert any("did not change" in line for line in evidence)
    assert observation.region_changed is False


def test_observation_notices_a_changed_region():
    assert Observation(text_before="a", text_after="b").region_changed is True


# --- the fault catalogue --------------------------------------------------------


def test_every_fault_states_the_real_world_cause():
    for key, fault in FAULTS.items():
        assert fault.name and fault.real_cause and fault.symptom
        assert len(fault.real_cause) > 40, f"{key} needs a real explanation, not a label"
        assert fault.difficulty in {"easy", "moderate", "hard"}
        assert callable(fault.apply)


def test_faults_expect_different_causes_and_tiers():
    """A catalogue where everything expects the same answer tests nothing."""
    causes = {f.expected_cause for f in FAULTS.values()}
    tiers = {f.expected_tier for f in FAULTS.values()}

    assert len(causes) >= 4, f"expected varied causes, got {causes}"
    assert len(tiers) >= 4, f"expected varied tiers, got {sorted(tiers)}"


def test_difficulty_is_spread_across_the_catalogue():
    levels = {f.difficulty for f in FAULTS.values()}
    assert levels == {"easy", "moderate", "hard"}


def test_difficulty_order_runs_easy_to_hard():
    order = difficulty_order()
    difficulties = [FAULTS[k].difficulty for k in order]
    rank = {"easy": 0, "moderate": 1, "hard": 2}
    assert difficulties == sorted(difficulties, key=lambda d: rank[d])


def test_blurb_names_both_the_fault_and_why_it_happens():
    blurb = FAULTS["consent_overlay"].blurb()
    assert "Consent banner" in blurb and "Why this happens in practice" in blurb
