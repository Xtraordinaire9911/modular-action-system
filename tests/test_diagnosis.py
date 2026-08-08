"""Recovery must be chosen from evidence, not from knowing which fault was set.

The property under test is that the same diagnosis function, given only what can
be observed after a failure, reaches different tiers for different situations.
If one fixed strategy came back every time, the demo would be a rehearsal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.demos.diagnosis import (
    CAUSE_INERT,
    CAUSE_MOVED,
    CAUSE_OBSCURED,
    CAUSE_VANISHED,
    STRATEGY_ESCALATE,
    STRATEGY_REROUTE,
    STRATEGY_RETRY,
    STRATEGY_ROLLBACK,
    diagnose,
)


@dataclass
class Box:
    x: int = 10
    y: int = 20
    w: int = 100
    h: int = 30

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class Mark:
    mark_id: str
    label: str
    bbox: Box = field(default_factory=Box)


def _finder(match: str):
    def find(marks: list[Any], goal: str):
        return next((m for m in marks if match.lower() in m.label.lower()), None)

    return find


TARGET = Mark("M002", "Add Wireless Headphones to cart")


# --- each situation reaches a different tier -----------------------------------


def test_moved_target_is_a_retry():
    moved = Mark("M002", "Add Wireless Headphones to cart", Box(x=10, y=400))
    result = diagnose(attempted=TARGET, fresh_marks=[moved], goal="add headphones", world_changed=True)

    assert result.cause == CAUSE_MOVED
    assert result.strategy == STRATEGY_RETRY and result.tier == 1


def test_vanished_target_with_an_alternative_is_a_reroute():
    fresh = [Mark("M009", "Buy Wireless Headphones now")]
    result = diagnose(
        attempted=TARGET,
        fresh_marks=fresh,
        goal="add headphones",
        world_changed=True,
        alternative_finder=_finder("headphones"),
    )

    assert result.cause == CAUSE_VANISHED
    assert result.strategy == STRATEGY_REROUTE and result.tier == 2
    assert result.alternative_label == "Buy Wireless Headphones now"


def test_vanished_target_without_an_alternative_escalates():
    result = diagnose(
        attempted=TARGET,
        fresh_marks=[Mark("M001", "Newsletter signup")],
        goal="add headphones",
        world_changed=True,
        alternative_finder=_finder("headphones"),
    )

    assert result.cause == CAUSE_VANISHED
    assert result.strategy == STRATEGY_ESCALATE and result.tier == 4


def test_action_with_no_effect_escalates_rather_than_retrying():
    """The silent failure: repeating an identical action repeats the outcome."""
    result = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="add headphones", world_changed=False)

    assert result.cause == CAUSE_INERT
    assert result.strategy == STRATEGY_ESCALATE and result.tier == 4
    assert any("behave identically" in line for line in result.evidence)


def test_something_changed_but_not_the_goal_rolls_back():
    result = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="add headphones", world_changed=True)

    assert result.cause == CAUSE_OBSCURED
    assert result.strategy == STRATEGY_ROLLBACK and result.tier == 3


def test_the_four_situations_do_not_collapse_to_one_strategy():
    """The whole point: different evidence, different tier."""
    moved = diagnose(
        attempted=TARGET,
        fresh_marks=[Mark("M002", TARGET.label, Box(y=500))],
        goal="g",
        world_changed=True,
    )
    rerouted = diagnose(
        attempted=TARGET,
        fresh_marks=[Mark("M009", "Buy headphones now")],
        goal="g",
        world_changed=True,
        alternative_finder=_finder("headphones"),
    )
    inert = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="g", world_changed=False)
    obscured = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="g", world_changed=True)

    tiers = {moved.tier, rerouted.tier, inert.tier, obscured.tier}
    assert len(tiers) == 4, f"expected four distinct tiers, got {sorted(tiers)}"


# --- the reasoning is inspectable ----------------------------------------------


def test_every_diagnosis_records_what_it_checked():
    result = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="g", world_changed=False)

    assert result.evidence, "a conclusion with no evidence cannot be argued with"
    assert any("re-observed" in line for line in result.evidence)
    assert any("looked for the element" in line for line in result.evidence)


def test_explanation_names_conclusion_strategy_and_tier():
    text = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="g", world_changed=False).explain()

    assert "what the agent checked" in text
    assert "conclusion:" in text and "strategy:" in text and "tier" in text


def test_serialised_diagnosis_keeps_the_evidence():
    data = diagnose(attempted=TARGET, fresh_marks=[TARGET], goal="g", world_changed=False).to_dict()

    assert data["cause"] == CAUSE_INERT
    assert data["tier"] == 4
    assert len(data["evidence"]) >= 3


def test_diagnosis_is_told_nothing_about_the_injected_fault():
    """Signature check: a diagnosis that could read the fault would prove nothing."""
    import inspect

    parameters = set(inspect.signature(diagnose).parameters)
    assert parameters == {"attempted", "fresh_marks", "goal", "world_changed", "alternative_finder"}
    assert not any("fault" in name or "scene" in name or "injected" in name for name in parameters)


def test_empty_page_escalates_rather_than_guessing():
    result = diagnose(attempted=TARGET, fresh_marks=[], goal="g", world_changed=True)
    assert result.strategy == STRATEGY_ESCALATE and result.tier == 4
