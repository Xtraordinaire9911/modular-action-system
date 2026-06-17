"""Tests for the goal-driven reflex policy used by the visual external runner."""

from __future__ import annotations

from src.benchmarks.reflex_policy import select_next
from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel


def _pam() -> PageAffordanceModel:
    return PageAffordanceModel(
        page_id="p",
        url="u",
        affordances=[
            Affordance("in_q", "DOM", "input", "Search", "type", {"selector": "#q"}, 0.9),
            Affordance("btn_go", "DOM", "button", "Submit Search", "click", {"selector": "#go"}, 1.0),
            Affordance("btn_cancel", "DOM", "button", "Cancel", "click", {"selector": "#x"}, 1.0),
        ],
    )


def test_fills_input_with_provided_value_first():
    aff, value = select_next(_pam(), "submit search", values={"Search": "shoes"})
    assert aff.id == "in_q" and value == "shoes"


def test_clicks_goal_relevant_button():
    aff, value = select_next(_pam(), "submit search")  # no input values → go to button
    assert aff.id == "btn_go" and value is None  # "submit"/"search" overlap beats "Cancel"


def test_skips_used_ids_and_falls_back():
    aff, _ = select_next(_pam(), "nothing matches", used_ids=("btn_go",))
    assert aff.id == "btn_cancel"  # first unused clickable when no goal overlap


def test_returns_none_when_exhausted():
    assert select_next(_pam(), "x", used_ids=("in_q", "btn_go", "btn_cancel")) is None


def test_skips_bare_label_affordances():
    pam = PageAffordanceModel(
        page_id="p",
        url="u",
        affordances=[
            Affordance(
                "dom_label_1", "DOM", "button", "Last reward:", "click", {"selector": "label:nth-of-type(1)"}, 0.5
            ),
            Affordance("btn_ok", "DOM", "button", "okay", "click", {"selector": "#ok"}, 1.0),
        ],
    )
    aff, _ = select_next(pam, "okay")
    assert aff.id == "btn_ok"  # the real button, never the bare <label>
    assert select_next(pam, "zzz", used_ids=("btn_ok",)) is None  # labels skipped in fallback too
