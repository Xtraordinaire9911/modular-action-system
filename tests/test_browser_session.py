"""Tests for the isolated browser session (Member B). No real browser needed."""

from __future__ import annotations

from src.perception.browser_session import BrowserSession

_HTML = """
<html><body>
  <button data-testid="book-room-button">Book Room</button>
  <input data-testid="room-input" name="room"/>
</body></html>
"""


class FakePage:
    """Minimal page driver that records interactions."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.url = ""
        self.calls: list[tuple] = []

    def goto(self, url: str) -> None:
        self.url = url
        self.calls.append(("goto", url))

    def content(self) -> str:
        return self._html

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))

    def click_xy(self, x: int, y: int) -> None:
        self.calls.append(("click_xy", x, y))

    def screenshot(self, **kwargs):
        self.calls.append(("screenshot", kwargs))
        return b"PNGDATA"


def test_state_transduces_live_page_into_pam():
    session = BrowserSession(FakePage(_HTML), url="http://localhost:3000")
    pam = session.state(page_id="booking_dashboard")
    assert pam.page_id == "booking_dashboard" and pam.url == "http://localhost:3000"
    assert pam.by_label("Book Room") is not None


def test_open_and_reset_navigate():
    page = FakePage(_HTML)
    session = BrowserSession(page, url="http://localhost:3000")
    session.open("http://localhost:3000/booking")
    session.reset()
    assert page.calls[0] == ("goto", "http://localhost:3000/booking")
    assert page.calls[-1] == ("goto", "http://localhost:3000/booking")


def test_session_satisfies_dom_and_pointer_protocols():
    page = FakePage(_HTML)
    session = BrowserSession(page)
    session.click("#book")
    session.fill("#room", "A")
    session.click_xy(100, 200)
    assert ("click", "#book") in page.calls
    assert ("fill", "#room", "A") in page.calls
    assert ("click_xy", 100, 200) in page.calls


def test_screenshot_returns_bytes():
    session = BrowserSession(FakePage(_HTML))
    assert session.screenshot() == b"PNGDATA"


def test_screenshot_hides_runtime_overlays_without_mutating_the_page():
    page = FakePage(_HTML)
    session = BrowserSession(page)

    assert session.screenshot("observation.png") == b"PNGDATA"

    _, kwargs = page.calls[-1]
    assert kwargs["path"] == "observation.png"
    assert kwargs["full_page"] is True
    assert kwargs["animations"] == "disabled"
    assert "#__cua_cursor" in kwargs["style"]
    assert "#__cua_cap" in kwargs["style"]
    assert "[data-agent-overlay='true']" in kwargs["style"]
    assert "[data-runtime-overlay='true']" in kwargs["style"]
    assert ".__cua_hl" in kwargs["style"]
