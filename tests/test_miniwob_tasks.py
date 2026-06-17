"""Offline tests for the MiniWoB++ demo suite (no browser)."""

from __future__ import annotations

from typing import Any

from src.benchmarks.miniwob_tasks import (
    MiniwobController,
    quoted_values,
    run_task,
    solve_click_button_sequence,
    solve_click_dialog,
    solve_click_link,
    solve_enter_password,
    solve_enter_text,
    solve_login_user,
)


def test_quoted_values():
    assert quoted_values('username "alice" password "pw1"') == ["alice", "pw1"]
    assert quoted_values("no quotes") == []


class RecordingController:
    """Duck-typed stand-in for MiniwobController capturing the solver's intent."""

    def __init__(self, utterance: str = "", reward: float = 1.0) -> None:
        self._q = utterance
        self._reward = reward
        self.calls: list[tuple] = []

    def start(self) -> None:
        self.calls.append(("start",))

    def query(self) -> str:
        return self._q

    def reward(self) -> float:
        return self._reward

    def fill(self, css: str, value: str, why: str) -> bool:
        self.calls.append(("fill", css, value))
        return True

    def click_css(self, css: str, why: str) -> bool:
        self.calls.append(("click", css))
        return True

    def click_text(self, scope: str, tag: str, text: str, why: str) -> bool:
        self.calls.append(("text", scope, tag, text))
        return True


def test_solver_call_sequences():
    c = RecordingController('Enter "hello" into the text field and press Submit.')
    solve_enter_text(c)
    assert c.calls == [("fill", "#tt", "hello"), ("click", "#subbtn")]

    c = RecordingController('Enter the username "alice" and the password "pw1" ... press login.')
    solve_login_user(c)
    assert c.calls == [("fill", "#username", "alice"), ("fill", "#password", "pw1"), ("click", "#subbtn")]

    c = RecordingController('Enter the password "s3cret" into both text fields and press submit.')
    solve_enter_password(c)
    assert c.calls == [("fill", "#password", "s3cret"), ("fill", "#verify", "s3cret"), ("click", "#subbtn")]

    c = RecordingController('Click on the link "Read more".')
    solve_click_link(c)
    assert c.calls == [("text", "#area", "a", "Read more")]

    c = RecordingController()
    solve_click_button_sequence(c)
    assert c.calls == [("click", "#subbtn"), ("click", "#subbtn2")]

    c = RecordingController()
    solve_click_dialog(c)
    assert c.calls == [("click", ".ui-dialog-titlebar-close")]


def test_run_task_starts_then_solves_then_scores():
    from src.benchmarks.miniwob_tasks import MiniwobDemoTask

    task = MiniwobDemoTask("enter-text", "Type text and submit", solve_enter_text)
    c = RecordingController('Enter "hi" into the text field and press Submit.', reward=0.97)
    outcome = run_task(c, task)
    assert c.calls[0] == ("start",)  # episode started before acting
    assert outcome["success"] and outcome["name"] == "enter-text" and outcome["reward"] == 0.97


class FakeSession:
    """Mimics BrowserSession: highlight resolves via querySelector, click records."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.fills.append((selector, value))

    def text_content(self, selector: str) -> str | None:
        return 'Enter "x" into the field.' if selector == "#query" else ""

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        if "querySelector(a.sel)" in expression:  # highlight: found?
            return arg["sel"] in self.known
        if "querySelectorAll(a.tag)" in expression:  # text resolve
            return "#__cua_target" if arg["text"] == "Yes" else None
        if expression == "WOB_REWARD_GLOBAL":
            return 1.0
        return None


def test_controller_skips_missing_selectors_safely():
    session = FakeSession(known={"#subbtn"})
    c = MiniwobController(session, step_delay=0.0, narrate=lambda _m: None)

    assert c.click_css("#subbtn", "ok") is True
    assert session.clicks == ["#subbtn"]

    assert c.click_css("#missing", "nope") is False  # not found -> skipped, no click, no hang
    assert session.clicks == ["#subbtn"]

    assert c.fill("#missing", "v", "nope") is False
    assert session.fills == []


def test_controller_click_text_resolves_then_clicks():
    session = FakeSession(known=set())
    c = MiniwobController(session, step_delay=0.0, narrate=lambda _m: None)
    assert c.click_text("#area", "a", "Yes", "click yes") is True
    assert session.clicks == ["#__cua_target"]
    assert c.click_text("#area", "a", "Missing", "x") is False
