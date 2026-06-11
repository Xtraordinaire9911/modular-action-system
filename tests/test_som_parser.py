"""Tests for Set-of-Marks visual grounding (Member B)."""

from __future__ import annotations

import pytest

from src.perception.som_parser import SetOfMarksParser

_REGIONS = [
    {"bbox": [410, 220, 110, 40], "label": "Book Room", "confidence": 0.93, "action": "click"},
    {"bbox": [520, 430, 160, 40], "label": "Target °C", "confidence": 0.81, "action": "type"},
    {"bbox": [10, 10, 30, 30], "label": "noise", "confidence": 0.2},
]


def test_marks_are_numbered_and_typed():
    affs = SetOfMarksParser().parse(_REGIONS, screenshot_ref="shot_001.png")
    assert [a.locator["mark_id"] for a in affs] == ["M0", "M1", "M2"]
    assert affs[0].source == "VISUAL" and affs[0].action == "click"
    assert affs[1].type == "input"  # type action → input
    assert affs[0].locator["center"] == [465, 240]


def test_low_confidence_regions_filtered():
    affs = SetOfMarksParser(min_confidence=0.5).parse(_REGIONS)
    assert [a.label for a in affs] == ["Book Room", "Target °C"]


def test_select_resolves_mark_id_to_target():
    affs = SetOfMarksParser().parse(_REGIONS)
    result = SetOfMarksParser().select(affs, "M0")
    assert result.label == "Book Room" and result.center == (465, 240)
    assert result.to_dict()["mark_id"] == "M0"


def test_select_unknown_mark_raises():
    affs = SetOfMarksParser().parse(_REGIONS)
    with pytest.raises(KeyError):
        SetOfMarksParser().select(affs, "M99")


def test_overlay_svg_contains_every_mark():
    affs = SetOfMarksParser(min_confidence=0.5).parse(_REGIONS)
    svg = SetOfMarksParser().render_overlay_svg(affs)
    assert svg.startswith("<svg") and ">M0<" in svg and ">M1<" in svg
