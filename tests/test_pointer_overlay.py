"""The overlay must be invisible to the agent and unable to fail a run.

Two properties matter and neither is cosmetic:

* **A broken overlay must not break a demo.** These helpers sit on the path of
  every browser demo. If a cursor that failed to draw could raise, a
  presentation would die for a reason unrelated to what it was presenting.
* **The overlay must not change what the agent perceives.** Everything it adds is
  ``pointer-events: none`` and carries a ``__cua_`` id. An element the DOM
  transducer could see would put the demo's own decoration into the affordance
  list, and a demo that changes the agent is not a demo of the agent.

The DOM-level guarantees are asserted against a real browser in the ``live``
tier; what is checked here is the contract around the JavaScript, which is where
a regression would be silent.
"""

from __future__ import annotations

from typing import Any

from src.demos import pointer_overlay as po


class _Recorder:
    """A session that records what it was asked to evaluate."""

    def __init__(self, result: Any = True) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._result = result

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        self.calls.append((expression, arg))
        return self._result


class _Exploding:
    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        raise RuntimeError("the page navigated away mid-highlight")


def test_a_failing_overlay_reports_false_instead_of_raising() -> None:
    """The reason this module exists as more than two string constants."""
    session = _Exploding()

    assert po.point_at_selector(session, "[data-testid='x']") is False
    assert po.point_at_box(session, x=1, y=2) is False
    assert po.clear_pointer(session) is False


def test_a_missing_element_is_false_rather_than_a_pointer_at_nothing() -> None:
    """``False`` is the interesting answer: the demo meant to point somewhere."""
    session = _Recorder(result=False)

    assert po.point_at_selector(session, "[data-testid='gone']") is False


def test_pointing_at_a_selector_lets_the_page_resolve_the_geometry() -> None:
    """One round trip, and "the element vanished" stays a plain False."""
    session = _Recorder()

    assert po.point_at_selector(session, "[data-testid='target-temp']", label="x <- 22") is True
    _, arg = session.calls[0]
    assert arg["selector"] == "[data-testid='target-temp']"
    assert arg["label"] == "x <- 22"
    # No coordinates: sending stale ones alongside a selector would leave it
    # ambiguous which the page should believe.
    assert "x" not in arg


def test_pointing_at_a_box_marks_the_rectangle_the_caller_measured() -> None:
    """Used where geometry comes from the transducer, so the ring is the exact
    rectangle the agent scored rather than one re-derived here."""
    session = _Recorder()

    assert po.point_at_box(session, x=40, y=50, box=(10, 20, 30, 40)) is True
    _, arg = session.calls[0]
    assert (arg["x"], arg["y"]) == (40, 50)
    assert (arg["bx"], arg["by"], arg["bw"], arg["bh"]) == (10, 20, 30, 40)
    assert "selector" not in arg


def test_a_box_is_optional_so_a_bare_position_hides_the_ring() -> None:
    session = _Recorder()

    assert po.point_at_box(session, x=5, y=6) is True
    _, arg = session.calls[0]
    assert "bx" not in arg


def test_every_element_the_overlay_adds_is_inert_and_namespaced() -> None:
    """Read off the JavaScript, because this is the property the agent depends on."""
    for element_id in ("__cua_cur", "__cua_ring", "__cua_label", "__cua_trail"):
        assert element_id in po.POINT_JS or element_id in po.CLEAR_JS

    # The invariant, rather than a hand-counted total that goes stale the moment
    # another element is added: every element this JS styles is unhittable.
    # Five today - the trail container, each trail dot, the cursor, the ring and
    # the label - but the assertion holds however many there are.
    styled = po.POINT_JS.count("cssText")
    assert styled > 0
    assert po.POINT_JS.count("pointer-events:none") == styled


def test_clear_removes_exactly_what_point_creates() -> None:
    """A leftover cursor would end up in the next screenshot the vision model is
    asked about, which is a way to make a model answer about our own overlay."""
    created = {i for i in ("__cua_cur", "__cua_ring", "__cua_label", "__cua_trail") if i in po.POINT_JS}
    removed = {i for i in ("__cua_cur", "__cua_ring", "__cua_label", "__cua_trail") if i in po.CLEAR_JS}

    assert created == removed


def test_the_trail_is_bounded_so_a_long_demo_cannot_accumulate_nodes() -> None:
    """A demo runs for minutes; an unbounded trail would keep appending to the
    page the agent is perceiving."""
    assert f"{po._TRAIL_MAX}" in po.POINT_JS
    assert "removeChild(t.firstChild)" in po.POINT_JS


def test_the_agent_colour_is_not_a_dashboard_colour() -> None:
    """The pointer must never read as part of the interface it is operating."""
    assert po.AGENT_COLOR.lower() == "#8383ff"
    # TUM blue is the dashboard's primary; the cursor deliberately is not it.
    assert po.AGENT_COLOR.lower() != "#0065bd"
