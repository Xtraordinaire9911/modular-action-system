"""Measure real element geometry from the live browser (Member B).

Set-of-Marks grounding needs boxes that describe what is actually on screen.
Until now the only source of ``locator["bbox"]`` was a ``data-bbox`` HTML
attribute, which exists solely in test fixtures: on a real page every affordance
came back without a box, so ``marks_from_affordances`` skipped all of them and
the visual path had nothing genuine to stand on.

This module asks the live page for ``getBoundingClientRect()`` instead, so marks
are derived from rendered geometry. Elements that cannot be measured (missing,
detached, or zero-sized) yield ``None`` and any previously attached box is
dropped rather than kept — an unmeasurable element must not carry a mark.

Coordinates are viewport-relative CSS pixels, which is the same frame as a
default (non-full-page) Playwright screenshot, so marks line up with the image.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

# Returns one entry per selector, in order: [x, y, w, h] or null when the element
# is absent, detached, or not rendered. Invalid selectors are caught so a single
# bad locator cannot abort the whole measurement pass.
_MEASURE_JS = (
    "(sels)=>sels.map((s)=>{"
    "let el=null;"
    "try{el=document.querySelector(s);}catch(e){return null;}"
    "if(!el)return null;"
    "const r=el.getBoundingClientRect();"
    "if(!(r.width>0&&r.height>0))return null;"
    "return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];"
    "})"
)

BBOX_SOURCE = "viewport_rect"


class _EvaluatingSession(Protocol):
    def evaluate(self, expression: str, arg: Any | None = ...) -> Any: ...


def _coerce_box(value: Any) -> list[int] | None:
    """Accept only a well-formed positive-area box; anything else is not a box."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    return [x, y, w, h] if w > 0 and h > 0 else None


def measure_bboxes(session: _EvaluatingSession, selectors: Sequence[str]) -> list[list[int] | None]:
    """Measure each selector in one round trip; unmeasurable entries are None."""
    if not selectors:
        return []
    try:
        raw = session.evaluate(_MEASURE_JS, list(selectors))
    except Exception:
        return [None] * len(selectors)  # a failed probe measures nothing; it invents nothing
    if not isinstance(raw, (list, tuple)):
        return [None] * len(selectors)
    boxes = [_coerce_box(item) for item in raw]
    # Defend against a driver returning a short/long array.
    boxes = boxes[: len(selectors)]
    boxes.extend([None] * (len(selectors) - len(boxes)))
    return boxes


def attach_measured_bboxes(pam: Any, session: _EvaluatingSession) -> int:
    """Replace affordance boxes with measured ones; return how many were measured.

    A stale or fixture-authored box is removed when the element cannot be
    measured, so downstream marks never describe geometry we did not observe.
    """
    affordances = list(getattr(pam, "affordances", []))
    selectors = [str(a.locator.get("selector", "")) for a in affordances]
    for affordance, box in zip(affordances, measure_bboxes(session, selectors)):
        if box is None:
            affordance.locator.pop("bbox", None)
            affordance.locator.pop("bbox_source", None)
            continue
        affordance.locator["bbox"] = box
        affordance.locator["bbox_source"] = BBOX_SOURCE
    return sum(1 for a in affordances if a.locator.get("bbox_source") == BBOX_SOURCE)
