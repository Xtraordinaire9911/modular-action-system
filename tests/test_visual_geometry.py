"""Visual marks must come from measured geometry, never from fixtures (Member B).

Before this layer existed the only bbox source was a ``data-bbox`` attribute that
appears exclusively in test HTML, so on a real page every affordance was skipped
by ``marks_from_affordances``. These tests pin the replacement contract: measured
boxes are attached, unmeasurable elements are left without a box, and a box that
was authored rather than observed is discarded.
"""

from __future__ import annotations

from typing import Any

from src.perception.dom_transducer import DomTransducer
from src.perception.som_parser import marks_from_affordances
from src.perception.visual_geometry import (
    BBOX_SOURCE,
    attach_measured_bboxes,
    measure_bboxes,
)


class RectSession:
    """Stands in for a live page: returns rects for known selectors only."""

    def __init__(self, rects: dict[str, list[int]], *, fail: bool = False) -> None:
        self.rects = rects
        self.fail = fail
        self.calls: list[list[str]] = []

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        if self.fail:
            raise RuntimeError("page closed")
        self.calls.append(list(arg or []))
        return [self.rects.get(sel) for sel in (arg or [])]


def _pam(html: str):
    return DomTransducer().transduce(html, page_id="p", url="http://localhost:3000")


# ── measure_bboxes ───────────────────────────────────────────────────────────────


def test_measures_known_selectors_in_one_round_trip():
    session = RectSession({"#a": [10, 20, 30, 40]})
    assert measure_bboxes(session, ["#a", "#missing"]) == [[10, 20, 30, 40], None]
    assert len(session.calls) == 1, "all selectors must be measured in a single evaluate"


def test_zero_area_and_malformed_rects_are_not_boxes():
    session = RectSession({"#hidden": [5, 5, 0, 12], "#bad": [1, 2, 3], "#ok": [0, 0, 8, 9]})
    assert measure_bboxes(session, ["#hidden", "#bad", "#ok"]) == [None, None, [0, 0, 8, 9]]


def test_probe_failure_measures_nothing_and_invents_nothing():
    session = RectSession({}, fail=True)
    assert measure_bboxes(session, ["#a", "#b"]) == [None, None]


def test_empty_selector_list_short_circuits():
    session = RectSession({})
    assert measure_bboxes(session, []) == []
    assert session.calls == []


# ── attach_measured_bboxes ───────────────────────────────────────────────────────


def test_attaches_measured_boxes_and_tags_their_source():
    pam = _pam("<html><body><button id='go'>Go</button></body></html>")
    session = RectSession({"#go": [12, 34, 56, 78]})

    assert attach_measured_bboxes(pam, session) == 1
    locator = pam.affordances[0].locator
    assert locator["bbox"] == [12, 34, 56, 78]
    assert locator["bbox_source"] == BBOX_SOURCE


def test_authored_bbox_is_discarded_when_the_element_cannot_be_measured():
    """The core guarantee: a mark must never describe geometry we did not observe."""
    pam = _pam("<html><body><button id='go' data-bbox='1,2,3,4'>Go</button></body></html>")
    assert pam.affordances[0].locator["bbox"] == [1, 2, 3, 4]  # fixture-authored

    assert attach_measured_bboxes(pam, RectSession({})) == 0
    assert "bbox" not in pam.affordances[0].locator
    assert "bbox_source" not in pam.affordances[0].locator


def test_authored_bbox_is_replaced_by_the_measured_one():
    pam = _pam("<html><body><button id='go' data-bbox='1,2,3,4'>Go</button></body></html>")
    attach_measured_bboxes(pam, RectSession({"#go": [90, 91, 92, 93]}))
    assert pam.affordances[0].locator["bbox"] == [90, 91, 92, 93]


# ── end-to-end into Set-of-Marks ─────────────────────────────────────────────────


def test_marks_are_built_only_from_measured_elements():
    html = "<html><body><button id='seen'>A</button><button id='unseen'>B</button></body></html>"
    pam = _pam(html)
    attach_measured_bboxes(pam, RectSession({"#seen": [4, 5, 60, 20]}))

    marks = marks_from_affordances(pam.affordances)
    # Only the measured element becomes a mark; the label follows the existing
    # transducer precedence (id before text), which this layer does not change.
    assert [m.label for m in marks] == ["seen"]
    assert marks[0].bbox.as_xywh() == [4, 5, 60, 20]
    assert marks[0].bbox.center == (34, 15)


def test_real_page_without_data_bbox_still_yields_marks():
    """Regression for the original defect: no data-bbox anywhere, marks still exist."""
    html = "<html><body><button data-testid='book'>Book</button></body></html>"
    pam = _pam(html)
    assert "bbox" not in pam.affordances[0].locator  # nothing authored, as on a real page

    attach_measured_bboxes(pam, RectSession({"[data-testid='book']": [7, 8, 100, 30]}))
    assert len(marks_from_affordances(pam.affordances)) == 1
