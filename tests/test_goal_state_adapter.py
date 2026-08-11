"""The runtime can only verify a goal it can observe.

Without the reported fact the condition evaluator raises "missing condition
path", the episode ends postcondition_failed, and a goal that was reached is
recorded as a failure. That is a false negative, and it is exactly as damaging
to the project's claims as a false success.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.planner.goal_state_adapter import GoalStateReportingAdapter


@dataclass
class _Observation:
    accessibility_tree: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Live:
    observation: _Observation


class _Inner:
    """Reports a page but says nothing about the goal, like the web adapter."""

    def __init__(self) -> None:
        self.reset_calls = 0
        self.observe_calls = 0

    async def reset(self, spec: Any) -> None:
        self.reset_calls += 1

    async def observe(self, request: Any) -> _Live:
        self.observe_calls += 1
        return _Live(observation=_Observation(accessibility_tree={"page_state": {"page": {"count": 7}}}))

    def executors(self) -> dict[str, Any]:
        return {"dom": object()}


def _observe(adapter: Any) -> Any:
    return asyncio.run(adapter.observe(object()))


def test_the_goal_fact_is_added_where_the_runtime_looks_for_it():
    inner = _Inner()
    adapter = GoalStateReportingAdapter(inner, fact=lambda ok: {"cart": {"holds_item": ok}}, holds=lambda: True)

    page_state = _observe(adapter).observation.accessibility_tree["page_state"]

    assert page_state["cart"] == {"holds_item": True}


def test_what_the_inner_adapter_reported_is_preserved():
    inner = _Inner()
    adapter = GoalStateReportingAdapter(inner, fact=lambda ok: {"cart": {"holds_item": ok}}, holds=lambda: False)

    page_state = _observe(adapter).observation.accessibility_tree["page_state"]

    assert page_state["page"] == {"count": 7}, "the wrapper must add, never replace"
    assert page_state["cart"] == {"holds_item": False}


def test_the_fact_is_re_read_on_every_observation():
    """A cached answer would report the goal as met before it was."""
    inner = _Inner()
    answers = iter([False, False, True])
    adapter = GoalStateReportingAdapter(
        inner, fact=lambda ok: {"cart": {"holds_item": ok}}, holds=lambda: next(answers)
    )

    seen = [_observe(adapter).observation.accessibility_tree["page_state"]["cart"]["holds_item"] for _ in range(3)]

    assert seen == [False, False, True]
    assert adapter.observations == [False, False, True]


def test_reset_and_executors_pass_straight_through():
    inner = _Inner()
    adapter = GoalStateReportingAdapter(inner, fact=lambda ok: {"cart": {"holds_item": ok}}, holds=lambda: True)

    asyncio.run(adapter.reset(object()))

    assert inner.reset_calls == 1
    assert adapter.executors() is not None and "dom" in adapter.executors()
