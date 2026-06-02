"""Unit tests for src/perception/som_parser.py."""

from src.perception.som_parser import (
    BoundingBox,
    VisualGroundingResult,
    VisualMark,
    marks_from_affordances,
    select_mark,
)

from src.contracts.types import Affordance


def _make_mark(mid: str, label: str, conf: float = 1.0) -> VisualMark:
    return VisualMark(
        mark_id=mid,
        label=label,
        bbox=BoundingBox(x=10, y=20, w=80, h=30),
        confidence=conf,
    )


def test_bbox_center():
    bb = BoundingBox(x=10, y=20, w=80, h=30)
    assert bb.center == (50, 35)


def test_bbox_as_list():
    bb = BoundingBox(x=10, y=20, w=80, h=30)
    assert bb.as_list() == [10, 20, 90, 50]


def test_select_mark_exact():
    marks = [_make_mark("M001", "Book Room"), _make_mark("M002", "Cancel")]
    result = select_mark(marks, "Book Room")
    assert result is not None
    assert result.mark_id == "M001"


def test_select_mark_partial():
    marks = [_make_mark("M001", "Book Room"), _make_mark("M002", "Cancel booking")]
    result = select_mark(marks, "book")
    assert result is not None


def test_select_mark_prefers_higher_confidence():
    marks = [
        _make_mark("M001", "submit button", conf=0.7),
        _make_mark("M002", "submit", conf=0.95),
    ]
    result = select_mark(marks, "submit")
    assert result is not None
    assert result.mark_id == "M002"


def test_select_mark_no_match_returns_none():
    marks = [_make_mark("M001", "Book Room")]
    assert select_mark(marks, "thermostat") is None


def test_select_mark_returns_grounding_result():
    marks = [_make_mark("M001", "Book Room")]
    result = select_mark(marks, "Book")
    assert isinstance(result, VisualGroundingResult)
    assert result.bbox == [10, 20, 90, 50]


def test_marks_from_affordances_with_bbox():
    aff = Affordance(
        id="dom_button_1",
        source="DOM",
        type="button",
        label="Book Room",
        action="click",
        locator={"selector": "#book-room", "bbox": [10, 20, 90, 50]},
        confidence=0.9,
    )
    marks = marks_from_affordances([aff])
    assert len(marks) == 1
    assert marks[0].label == "Book Room"


def test_marks_from_affordances_skips_no_bbox():
    aff = Affordance(
        id="dom_input_1",
        source="DOM",
        type="input",
        label="Room ID",
        action="type",
        locator={"selector": "#room-input"},
        confidence=1.0,
    )
    marks = marks_from_affordances([aff])
    assert marks == []
