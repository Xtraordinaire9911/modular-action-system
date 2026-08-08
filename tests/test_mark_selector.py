"""Set-of-Marks selection must be model-driven when a model is configured,
and must never dress the deterministic scorer up as a decision.

The numbering exists so a model can answer with an identifier instead of pixel
coordinates. The property that matters is therefore not accuracy but honesty
about which path produced the answer - and a refusal to act on an identifier
that was never offered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.planner.mark_selector import MarkSelector, describe_marks


@dataclass
class FakeBox:
    x: int = 10
    y: int = 20
    w: int = 100
    h: int = 30

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class FakeMark:
    mark_id: str
    label: str
    confidence: float = 1.0
    bbox: FakeBox = field(default_factory=FakeBox)
    extra: dict[str, Any] = field(default_factory=lambda: {"action": "click"})


class FakeClient:
    def __init__(self, reply: str, *, name: str = "fake-model") -> None:
        self.name = name
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.reply


class BrokenClient:
    name = "broken"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("model unavailable")


MARKS = [
    FakeMark("M000", "Search products"),
    FakeMark("M002", "Add Wireless Headphones to cart"),
    FakeMark("M006", "Proceed to checkout"),
]


def _reply(mark_id: str = "M002", reason: str = "it adds the requested product", confidence: float = 0.92) -> str:
    return json.dumps({"mark_id": mark_id, "reason": reason, "confidence": confidence})


def _selector(client, tmp_path: Path) -> MarkSelector:
    return MarkSelector(client=client, ledger_path=tmp_path / "calls.jsonl")


# --- the model path -------------------------------------------------------------


def test_model_choice_is_resolved_to_the_actual_mark(tmp_path):
    selection = _selector(FakeClient(_reply()), tmp_path).select(MARKS, "add the headphones to the cart")

    assert selection.is_model_derived is True
    assert selection.mark_id == "M002"
    assert selection.mark is MARKS[1], "the chosen id must resolve to the mark object"
    assert selection.confidence == 0.92


def test_the_models_own_reason_is_kept(tmp_path):
    """The choice should be arguable, not merely observable."""
    selection = _selector(FakeClient(_reply(reason="only this one names the product")), tmp_path).select(
        MARKS, "add headphones"
    )
    assert "only this one names the product" in selection.reason


def test_every_candidate_is_offered_to_the_model(tmp_path):
    client = FakeClient(_reply())
    _selector(client, tmp_path).select(MARKS, "add headphones")

    prompt = client.prompts[0]
    for mark in MARKS:
        assert mark.mark_id in prompt and mark.label in prompt


def test_a_model_may_decline(tmp_path):
    selection = _selector(
        FakeClient(_reply(mark_id="none", reason="nothing here archives a message")), tmp_path
    ).select(MARKS, "archive the message")

    assert selection.ok is False and selection.source == "none"
    assert "archives a message" in selection.reason


def test_an_invented_identifier_is_refused(tmp_path):
    """Acting on a hallucinated id would be worse than not acting."""
    selection = _selector(FakeClient(_reply(mark_id="M999")), tmp_path).select(MARKS, "add headphones")

    assert selection.ok is False
    assert "not offered" in selection.error


def test_unparseable_reply_falls_back_and_says_so(tmp_path):
    selection = _selector(FakeClient("no json here"), tmp_path).select(MARKS, "add headphones")

    assert selection.is_model_derived is False
    assert selection.error


# --- the heuristic path is never disguised --------------------------------------


def test_without_a_model_the_source_is_heuristic(tmp_path):
    selection = _selector(None, tmp_path).select(MARKS, "add Headphones")

    assert selection.source == "heuristic"
    assert selection.is_model_derived is False
    assert selection.ok is True, "the deterministic scorer should still pick something sensible"
    assert selection.mark_id == "M002"


def test_heuristic_reason_carries_the_scoring_breakdown(tmp_path):
    """Both paths explain themselves in the same field."""
    selection = _selector(None, tmp_path).select(MARKS, "add Headphones")
    assert "candidates considered" in selection.reason


def test_model_failure_falls_back_without_claiming_the_model(tmp_path):
    selection = _selector(BrokenClient(), tmp_path).select(MARKS, "add Headphones")

    assert selection.is_model_derived is False
    assert selection.source == "heuristic"
    assert "model unavailable" in selection.error


def test_empty_page_is_reported_not_guessed(tmp_path):
    selection = _selector(FakeClient(_reply()), tmp_path).select([], "add headphones")

    assert selection.ok is False and selection.source == "none"
    assert "nothing interactive" in selection.reason


# --- the audit ledger -----------------------------------------------------------


def test_model_calls_are_logged_with_candidates_and_reply(tmp_path):
    ledger = tmp_path / "calls.jsonl"
    MarkSelector(client=FakeClient(_reply()), ledger_path=ledger).select(MARKS, "add headphones")

    record = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert record["mark_id"] == "M002"
    assert record["source"] == "llm"
    assert "M006" in record["candidates"], "the whole offered set must be recoverable"
    assert record["raw_response"]


def test_logging_failure_never_breaks_selection(tmp_path):
    selector = MarkSelector(client=FakeClient(_reply()), ledger_path=Path("\0invalid"))
    assert selector.select(MARKS, "add headphones").ok is True


# --- the candidate listing ------------------------------------------------------


def test_listing_names_id_label_action_and_position():
    listing = describe_marks(MARKS)
    assert "M002" in listing and "Add Wireless Headphones to cart" in listing
    assert "[click]" in listing and "(60," in listing
