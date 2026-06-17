"""Offline tests for the MiniWoB++ task-aware driver (no browser)."""

from __future__ import annotations

from typing import Any

from src.benchmarks.miniwob_adapter import MiniwobAdapter, parse_target


def test_parse_target_extracts_quoted_word():
    assert parse_target('Click on the "okay" button.') == "okay"
    assert parse_target('Enter "John Smith" and submit') == "John Smith"
    assert parse_target("no quotes here") is None
    assert parse_target("") is None


class FakeMiniwobSession:
    """Mimics the MiniWoB DOM contract: START gate, #query, #area, JS globals."""

    def __init__(self, correct: str = "okay") -> None:
        self._correct = correct
        self.started = False
        self.clicked: str | None = None
        self.calls: list[str] = []

    def click(self, selector: str) -> None:
        self.calls.append(selector)
        if selector == "#sync-task-cover":
            self.started = True
        elif selector.startswith("#area button"):
            self.clicked = parse_target(selector)  # ':text-is("X")' -> X

    def text_content(self, selector: str) -> str | None:
        if selector == "#query" and self.started:
            return f'Click on the "{self._correct}" button.'
        return ""

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        if expression == "WOB_REWARD_GLOBAL":
            return 1.0 if self.clicked == self._correct else -1.0
        if expression == "WOB_DONE_GLOBAL":
            return self.clicked is not None
        return None


def test_run_click_button_solves_and_reads_reward():
    session = FakeMiniwobSession(correct="okay")
    outcome = MiniwobAdapter(session).run_click_button()
    assert outcome["success"] and outcome["target"] == "okay" and outcome["reward"] == 1.0
    # START first, then the exact-text button click.
    assert session.calls[0] == "#sync-task-cover"
    assert session.calls[1] == '#area button:text-is("okay")'


def test_run_click_button_misses_on_wrong_reward():
    # Session whose reward is negative (agent would have clicked the wrong button).
    session = FakeMiniwobSession(correct="okay")
    session._correct = "okay"  # query says okay...

    class WrongClickSession(FakeMiniwobSession):
        def click(self, selector: str) -> None:
            self.calls.append(selector)
            if selector == "#sync-task-cover":
                self.started = True
            elif selector.startswith("#area button"):
                self.clicked = "cancel"  # clicked the wrong one

    outcome = MiniwobAdapter(WrongClickSession(correct="okay")).run_click_button()
    assert not outcome["success"] and outcome["reward"] == -1.0
