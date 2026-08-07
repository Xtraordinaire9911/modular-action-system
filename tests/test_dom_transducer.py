"""Tests for the DOM Transduction Pattern (Member B)."""

from __future__ import annotations

from src.perception.dom_transducer import DomTransducer

_DASHBOARD = """
<html><head><style>.x{}</style><script>track()</script></head>
<body>
  <h1>Smart Room A</h1>
  <button data-testid="book-room-button">Book Room</button>
  <input data-testid="temperature-input" name="temp" type="number" placeholder="Target °C"/>
  <button id="apply-temperature" data-bbox="520,430,160,40">Apply</button>
  <button disabled aria-label="Locked">Override</button>
  <a href="/help">Help</a>
  <script>console.log('noise')</script>
</body></html>
"""


def _pam():
    return DomTransducer().transduce(_DASHBOARD, page_id="booking_dashboard", url="http://localhost:3000")


def test_strips_script_and_style_nodes():
    pam = _pam()
    labels = {a.label for a in pam.affordances}
    assert "track()" not in labels and "console.log('noise')" not in labels


def test_extracts_interactive_elements_with_actions():
    pam = _pam()
    book = pam.by_label("Book Room")
    assert book is not None and book.action == "click" and book.source == "DOM"
    temp = next(a for a in pam.affordances if a.locator["selector"] == "[data-testid='temperature-input']")
    assert temp.action == "type" and temp.type == "input"


def test_selector_preference_drives_confidence():
    pam = _pam()
    book = pam.by_label("Book Room")  # data-testid hook
    apply_btn = pam.by_id("dom_button_2")  # has #id
    assert apply_btn.locator["selector"] == "#apply-temperature"
    assert apply_btn.confidence == 1.0  # id is the most stable
    assert book.confidence == 0.97  # testid slightly below id


def test_disabled_element_has_zero_confidence_and_state():
    pam = _pam()
    override = pam.by_label("Locked")
    assert override.confidence == 0.0
    assert override.state["enabled"] is False


def test_data_bbox_hint_is_parsed():
    pam = _pam()
    apply_btn = pam.by_id("dom_button_2")
    assert apply_btn.locator["bbox"] == [520, 430, 160, 40]


def test_compression_ratio_reported():
    pam = _pam()
    assert pam.kept_node_count < pam.raw_node_count
    assert 0.0 < pam.compression_ratio < 1.0
    assert pam.to_dict()["page_id"] == "booking_dashboard"


def test_runtime_overlay_elements_and_their_descendants_are_excluded():
    html = """
    <button id="before" aria-label="Real before">Real before</button>
    <div id="__cua_cursor" role="button" aria-label="Cursor overlay">
      <button id="nested-cursor-action">Nested cursor action</button>
    </div>
    <button id="__cua_cap">Caption overlay</button>
    <section data-agent-overlay="true">
      <a href="/agent-only">Agent overlay link</a>
    </section>
    <input data-runtime-overlay="true" aria-label="Runtime overlay input">
    <button id="after" aria-label="Real after">Real after</button>
    """

    pam = DomTransducer().transduce(html)

    assert [affordance.label for affordance in pam.affordances] == ["Real before", "Real after"]
    assert [affordance.locator["selector"] for affordance in pam.affordances] == ["#before", "#after"]


def test_void_elements_inside_runtime_overlays_do_not_hide_following_affordances():
    html = """
    <div data-runtime-overlay="true">
      <img src="cursor.png" alt="Overlay cursor">
    </div>
    <div data-agent-overlay="true">
      <img src="badge.png" alt="Overlay badge" />
      <button aria-label="Nested overlay action">Ignore me</button>
    </div>
    <button id="real-action" aria-label="Real action">Run</button>
    """

    pam = DomTransducer().transduce(html)

    assert [affordance.label for affordance in pam.affordances] == ["Real action"]
    assert pam.affordances[0].locator["selector"] == "#real-action"
