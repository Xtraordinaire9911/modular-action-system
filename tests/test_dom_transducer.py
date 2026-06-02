"""Unit tests for src/perception/dom_transducer.py."""

from src.perception.dom_transducer import PageAffordanceModel, parse_html

_SAMPLE_HTML = """
<html>
<head><title>Booking</title></head>
<script>var x = 1;</script>
<body>
  <form id="booking-form">
    <input id="room-input" name="room" type="text" placeholder="Room ID" />
    <input id="time-input" name="time" type="text" placeholder="Time slot" />
    <button id="book-room" type="submit">Book Room</button>
  </form>
  <a href="/cancel" id="cancel-link">Cancel</a>
</body>
</html>
"""


def test_parse_returns_pam():
    pam = parse_html(_SAMPLE_HTML, page_id="booking_dashboard")
    assert isinstance(pam, PageAffordanceModel)
    assert pam.page_id == "booking_dashboard"


def test_interactive_elements_extracted():
    pam = parse_html(_SAMPLE_HTML)
    labels = [a.label for a in pam.affordances]
    assert any("room" in lbl.lower() for lbl in labels)
    assert any("time" in lbl.lower() or "time" in lbl.lower() for lbl in labels)
    assert any("book" in lbl.lower() for lbl in labels)


def test_script_tags_stripped():
    pam = parse_html(_SAMPLE_HTML)
    for aff in pam.affordances:
        assert "script" not in aff.locator.get("selector", "").lower()


def test_all_affordances_have_dom_source():
    pam = parse_html(_SAMPLE_HTML)
    for aff in pam.affordances:
        assert aff.source == "DOM"
        assert aff.confidence == 1.0


def test_find_by_label():
    pam = parse_html(_SAMPLE_HTML)
    result = pam.find_by_label("Book Room")
    assert result is not None
    assert result.action == "click"


def test_find_by_selector():
    pam = parse_html(_SAMPLE_HTML)
    result = pam.find_by_selector("#book-room")
    assert result is not None


def test_id_preferred_in_locator():
    pam = parse_html(_SAMPLE_HTML)
    book_btn = pam.find_by_label("Book Room")
    assert book_btn is not None
    assert book_btn.locator["selector"] == "#book-room"


def test_empty_html_returns_empty_pam():
    pam = parse_html("<html></html>")
    assert pam.affordances == []


def test_input_action_is_type():
    pam = parse_html(_SAMPLE_HTML)
    room_inp = pam.find_by_selector("#room-input")
    assert room_inp is not None
    assert room_inp.action == "type"
