"""Offline tests for the WebArena-style mock environment tasks (no browser)."""

from __future__ import annotations

from src.benchmarks.miniwob_tasks import MockEnvController
from src.benchmarks.mock_env_tasks import (
    MOCK_TASKS,
    MockEnvTask,
    solve_email_archive_bob,
    solve_email_reply_alice,
    solve_forum_new_post,
    solve_forum_upvote_top,
    solve_shopping_add_checkout,
    solve_shopping_search_add,
)


# Reuse the same recording shim pattern as test_miniwob_tasks.py
class RecordingController:
    """Duck-typed stand-in capturing solver call sequences."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def start(self) -> None:
        self.calls.append(("start",))

    def query(self) -> str:
        return ""

    def reward(self) -> float:
        return 0.0

    def setup_badge(self, env: str, task: str) -> None:  # cosmetic — record for completeness
        self.calls.append(("badge", env, task))

    def fill(self, css: str, value: str, why: str) -> bool:
        self.calls.append(("fill", css, value))
        return True

    def click_css(self, css: str, why: str) -> bool:
        self.calls.append(("click", css))
        return True

    def click_text(self, scope: str, tag: str, text: str, why: str) -> bool:
        self.calls.append(("text", scope, tag, text))
        return True


# ── task list structure ──────────────────────────────────────────────────────────

def test_mock_tasks_nonempty_and_valid():
    assert len(MOCK_TASKS) == 6
    for t in MOCK_TASKS:
        assert isinstance(t, MockEnvTask)
        assert t.name
        assert t.env_label
        assert t.html_path.endswith(".html")
        assert callable(t.solve)


def test_mock_tasks_names_unique():
    names = [t.name for t in MOCK_TASKS]
    assert len(names) == len(set(names)), "Task names must be unique."


def test_mock_tasks_html_paths():
    # All html_path values should be bare filenames (no leading slash/path)
    for t in MOCK_TASKS:
        assert "/" not in t.html_path and "\\" not in t.html_path


# ── MockEnvController no-gate contract ──────────────────────────────────────────

class FakeSession:
    """Minimal BrowserSession duck-type: no real browser, evaluate always returns None."""

    def __init__(self) -> None:
        self.clicks: list[str] = []
        self.fills: list[tuple] = []

    def click(self, css: str) -> None:
        self.clicks.append(css)

    def fill(self, css: str, value: str) -> None:
        self.fills.append((css, value))

    def text_content(self, selector: str) -> str | None:
        return ""

    def evaluate(self, expression: str, arg=None):
        # highlight: querySelector found → True so click proceeds without skipping
        if "querySelector(a.sel)" in expression:
            return True
        return None


def test_mock_controller_no_gate_click():
    """MockEnvController.start() must NOT click #sync-task-cover."""
    session = FakeSession()
    ctrl = MockEnvController(session, step_delay=0.0, narrate=lambda _: None)
    ctrl.start()
    # No clicks should have been issued (gate is absent on mock pages)
    assert session.clicks == []


def test_mock_controller_query_returns_empty():
    session = FakeSession()
    ctrl = MockEnvController(session, step_delay=0.0, narrate=lambda _: None)
    assert ctrl.query() == ""


def test_mock_controller_reward_is_zero():
    session = FakeSession()
    ctrl = MockEnvController(session, step_delay=0.0, narrate=lambda _: None)
    assert ctrl.reward() == 0.0


# ── solver call-sequence contracts ───────────────────────────────────────────────

def test_shopping_add_checkout_sequence():
    c = RecordingController()
    solve_shopping_add_checkout(c)
    assert c.calls == [
        ("click", "button.add-cart-btn[data-id='headphones']"),
        ("click", "button#checkout-btn"),
    ]


def test_shopping_search_add_sequence():
    c = RecordingController()
    solve_shopping_search_add(c)
    assert c.calls == [
        ("fill", "input#search-input", "laptop"),
        ("click", "button#search-btn"),
        ("click", "button.add-cart-btn[data-id='laptop']"),
        ("click", "button#checkout-btn"),
    ]


def test_email_reply_alice_sequence():
    c = RecordingController()
    solve_email_reply_alice(c)
    assert c.calls[0] == ("click", "div.email-item[data-id='alice']")
    assert c.calls[1] == ("click", "button#reply-btn")
    assert c.calls[2][0] == "fill" and c.calls[2][1] == "textarea#reply-input"
    assert c.calls[3] == ("click", "button#send-btn")


def test_email_archive_bob_sequence():
    c = RecordingController()
    solve_email_archive_bob(c)
    assert c.calls == [
        ("click", "div.email-item[data-id='bob']"),
        ("click", "button#archive-btn"),
    ]


def test_forum_upvote_sequence():
    c = RecordingController()
    solve_forum_upvote_top(c)
    assert c.calls == [("click", "button.upvote-btn[data-post='1']")]


def test_forum_new_post_sequence():
    c = RecordingController()
    solve_forum_new_post(c)
    assert c.calls[0] == ("fill", "input#post-title", "Hello from the Agent!")
    assert c.calls[1][0] == "fill" and c.calls[1][1] == "textarea#post-content"
    assert c.calls[2] == ("click", "button#submit-post-btn")


# ── success_text contract ────────────────────────────────────────────────────────

def test_success_text_values():
    by_name = {t.name: t for t in MOCK_TASKS}
    assert by_name["shopping-add-checkout"].success_text == "order confirmed"
    assert by_name["shopping-search-add"].success_text == "order confirmed"
    assert "sent" in by_name["email-reply-alice"].success_text.lower()
    assert by_name["email-archive-bob"].success_text == ""    # visual-only
    assert by_name["forum-upvote"].success_text == ""         # visual-only
    assert by_name["forum-new-post"].success_text != ""       # has token
