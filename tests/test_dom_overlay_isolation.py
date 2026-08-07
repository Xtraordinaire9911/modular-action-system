"""Perception must ignore our own demo-overlay markers (Member B).

The headed demo tags live page elements with __cua_* ids/classes (cursor,
highlight ring, resolved-target marker). Without filtering, the transducer
derives locators from those markers, so the PAM changes on every demo step and
reports selectors that do not exist outside a demo run.
"""

from __future__ import annotations

from src.perception.dom_transducer import DomTransducer

# Same page, twice: once clean, once mid-demo with overlay markers applied.
_CLEAN = """
<html><body>
  <button data-testid="book-room-button">Book Room</button>
  <a href="/help">Help</a>
  <button class="primary wide">Apply</button>
  <input name="temp" type="number"/>
</body></html>
"""

_WITH_OVERLAY = """
<html><body>
  <div id="__cua_cursor"></div><div id="__cua_badge">MiniWoB++</div>
  <button data-testid="book-room-button" class="__cua_hl">Book Room</button>
  <a href="/help" id="__cua_target">Help</a>
  <button class="__cua_hl primary wide">Apply</button>
  <input name="temp" type="number" class="__cua_hl"/>
</body></html>
"""


def _selectors(html: str) -> list[str]:
    pam = DomTransducer().transduce(html, page_id="p", url="http://localhost:3000")
    return [a.locator["selector"] for a in pam.affordances]


def _labels(html: str) -> list[str]:
    pam = DomTransducer().transduce(html, page_id="p", url="http://localhost:3000")
    return [a.label for a in pam.affordances]


def test_overlay_markers_do_not_change_derived_selectors():
    """Core acceptance: a demo overlay must not alter perception output at all."""
    assert _selectors(_WITH_OVERLAY) == _selectors(_CLEAN)


def test_no_overlay_marker_leaks_into_selectors_or_labels():
    out = _selectors(_WITH_OVERLAY) + _labels(_WITH_OVERLAY)
    assert not any("__cua_" in value for value in out)


def test_injected_overlay_divs_are_not_affordances():
    # The cursor/badge divs are non-interactive, so they must never be actionable.
    assert all("cursor" not in s and "badge" not in s for s in _selectors(_WITH_OVERLAY))


def test_real_identifiers_survive_overlay_stripping():
    selectors = _selectors(_WITH_OVERLAY)
    # data-testid and name are genuine page attributes and must still win.
    assert "[data-testid='book-room-button']" in selectors
    assert "input[name='temp']" in selectors


def test_overlay_only_class_falls_through_instead_of_locking_on_marker():
    # A real element whose *only* class is our marker must fall through to the
    # next genuine strategy, not emit "tag.__cua_hl".
    html = "<html><body><button class='__cua_hl'>Go</button></body></html>"
    assert _selectors(html) == ["button:nth-of-type(1)"]


def test_overlay_id_falls_through_to_next_strategy():
    # "#__cua_target" would otherwise be emitted with the highest confidence.
    html = "<html><body><a id='__cua_target' href='/x' data-testid='real'>Go</a></body></html>"
    assert _selectors(html) == ["[data-testid='real']"]
