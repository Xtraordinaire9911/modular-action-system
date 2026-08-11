"""The decision record has to be auditable, and honest about being deterministic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.demos.deliberation import W_LABEL, Decision, deliberate


@dataclass
class FakeBox:
    x: int = 10
    y: int = 10
    w: int = 120
    h: int = 32


@dataclass
class FakeMark:
    mark_id: str
    label: str
    confidence: float = 1.0
    bbox: FakeBox = field(default_factory=FakeBox)
    extra: dict[str, Any] = field(default_factory=lambda: {"action": "click"})


def _marks(*pairs: tuple[str, str]) -> list[FakeMark]:
    return [FakeMark(mark_id=m, label=label) for m, label in pairs]


# --- the ranking ---------------------------------------------------------------


def test_every_candidate_is_recorded_not_just_the_winner():
    """The point of the record: the alternatives are inspectable afterwards."""
    decision = deliberate(
        _marks(("M0", "Add Headphones to cart"), ("M1", "Checkout"), ("M2", "Search")), "Add Headphones"
    )

    assert decision.considered == 3
    assert decision.chosen is not None and decision.chosen.mark_id == "M0"
    assert [c.mark_id for c in decision.candidates] == ["M0", "M1", "M2"] or len(decision.candidates) == 3


def test_ranking_is_sorted_by_score():
    decision = deliberate(_marks(("M0", "Checkout"), ("M1", "Add Headphones to cart")), "Add Headphones")
    scores = [c.score for c in decision.candidates]
    assert scores == sorted(scores, reverse=True)


def test_winner_carries_the_terms_that_produced_its_score():
    decision = deliberate(
        _marks(
            ("M0", "Add Headphones to cart"),
        ),
        "Add Headphones",
    )
    assert decision.chosen is not None
    assert set(decision.chosen.terms) == {"label", "action", "confidence", "size"}
    assert abs(sum(decision.chosen.terms.values()) - decision.chosen.score) < 1e-9


def test_margin_is_the_gap_to_the_next_option():
    decision = deliberate(_marks(("M0", "Add Headphones to cart"), ("M1", "Add Laptop to cart")), "Add Headphones")
    assert decision.chosen is not None and decision.chosen.mark_id == "M0"
    assert decision.margin > 0, "a clearly better match must win by a positive margin"


# --- rejections are explained --------------------------------------------------


def test_irrelevant_candidate_is_rejected_with_a_reason():
    decision = deliberate(_marks(("M0", "Add Headphones to cart"), ("M1", "Newsletter signup")), "Add Headphones")
    loser = next(c for c in decision.candidates if c.mark_id == "M1")
    assert "rejected" in loser.verdict and "label" in loser.verdict


def test_nothing_is_chosen_when_no_label_matches():
    decision = deliberate(_marks(("M0", "Checkout"), ("M1", "Search")), "Archive Bob's message")
    assert decision.chosen is None
    assert decision.considered == 2, "candidates are still recorded even when none qualify"


def test_empty_page_explains_itself_rather_than_crashing():
    decision = deliberate([], "Add Headphones")
    assert decision.chosen is None
    assert "no interactive element" in decision.explain()


# --- scoring terms behave as documented ----------------------------------------


def test_label_term_scales_with_how_much_of_the_goal_is_present():
    full = deliberate(
        _marks(
            ("M0", "Add Wireless Headphones to cart"),
        ),
        "Add Wireless Headphones",
    )
    half = deliberate(
        _marks(
            ("M0", "Add Wireless Speaker to cart"),
        ),
        "Add Wireless Headphones",
    )
    assert full.candidates[0].terms["label"] > half.candidates[0].terms["label"]
    assert full.candidates[0].terms["label"] <= W_LABEL


def test_a_sliver_earns_no_size_credit():
    tiny = FakeMark("M0", "Add Headphones to cart", bbox=FakeBox(w=3, h=3))
    assert deliberate([tiny], "Add Headphones").candidates[0].terms["size"] == 0.0


def test_a_full_page_container_earns_no_size_credit():
    container = FakeMark("M0", "Add Headphones to cart", bbox=FakeBox(w=1280, h=800))
    assert deliberate([container], "Add Headphones").candidates[0].terms["size"] == 0.0


def test_wrong_action_type_loses_its_action_credit():
    text_field = FakeMark("M0", "Add Headphones to cart", extra={"action": "type"})
    assert deliberate([text_field], "Add Headphones").candidates[0].terms["action"] == 0.0


# --- the explanation a viewer reads --------------------------------------------


def test_explanation_names_the_goal_the_winner_and_the_margin():
    decision = deliberate(_marks(("M0", "Add Headphones to cart"), ("M1", "Checkout")), "Add Headphones")
    text = decision.explain()

    assert "Add Headphones" in text
    assert "candidates considered" in text
    assert "why the winner won" in text
    assert "margin over runner-up" in text


def test_serialised_decision_keeps_the_whole_ranking():
    decision = deliberate(_marks(("M0", "Add Headphones to cart"), ("M1", "Checkout")), "Add Headphones")
    data = decision.to_dict()

    assert data["chosen"] == "M0"
    assert len(data["ranking"]) == 2
    assert all("terms" in row and "verdict" in row for row in data["ranking"])


def test_decision_is_reproducible():
    """Deterministic by construction; no sampling, so two runs must agree."""
    marks = _marks(("M0", "Add Headphones to cart"), ("M1", "Add Laptop to cart"))
    assert deliberate(marks, "Add Headphones").to_dict() == deliberate(marks, "Add Headphones").to_dict()


def test_decision_type_is_exported_for_callers():
    assert isinstance(deliberate([], "x"), Decision)
