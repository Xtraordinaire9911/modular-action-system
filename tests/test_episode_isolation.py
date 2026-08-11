"""Episode boundaries must recreate the context, not just re-navigate (Member B).

``reset()`` was the only per-run entry point, but it is navigation only: cookies,
localStorage and sessionStorage all survive it, so one episode could observe what
a previous episode wrote. These tests pin the distinction and the rollback path.
"""

from __future__ import annotations

from typing import Any

from src.perception.browser_session import BrowserSession


class FakePage:
    def __init__(self) -> None:
        self.gotos: list[str] = []
        self.timeout: int | None = None

    def goto(self, url: str) -> None:
        self.gotos.append(url)

    def set_default_timeout(self, ms: int) -> None:
        self.timeout = ms

    def content(self) -> str:
        return "<html><body></body></html>"


class FakeContext:
    def __init__(self, kwargs: dict[str, Any], state: dict[str, Any] | None = None) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.pages: list[FakePage] = []
        self._state = state or {"cookies": [], "origins": []}

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True

    def storage_state(self) -> dict[str, Any]:
        return self._state


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []

    def new_context(self, **kwargs: Any) -> FakeContext:
        context = FakeContext(kwargs)
        self.contexts.append(context)
        return context


def _session(state: dict[str, Any] | None = None) -> tuple[BrowserSession, FakeBrowser, FakeContext]:
    browser = FakeBrowser()
    context = FakeContext({}, state)
    browser.contexts.append(context)
    page = context.new_page()
    session = BrowserSession(page, url="http://x/start", _owner=(object(), browser, context), action_timeout_ms=4321)
    return session, browser, context


# ── the boundary ─────────────────────────────────────────────────────────────────


def test_new_episode_replaces_the_context():
    session, browser, first = _session()

    assert session.new_episode() is True
    assert first.closed, "the previous isolation boundary must be dropped"
    assert len(browser.contexts) == 2, "a fresh context is the boundary"
    assert browser.contexts[-1] is not first


def test_reset_does_not_cross_an_episode_boundary():
    """reset() is navigation only — proving why new_episode() had to exist."""
    session, browser, first = _session()

    session.reset()

    assert not first.closed
    assert len(browser.contexts) == 1, "reset must not create a new context"
    assert session.episode_index == 0


def test_new_episode_starts_without_carried_over_storage():
    session, browser, _ = _session()
    session.new_episode()
    # No storage_state passed -> the fresh context starts empty, which is what
    # drops cookies/localStorage between episodes.
    assert "storage_state" not in browser.contexts[-1].kwargs


def test_episode_index_tracks_boundaries():
    session, _, _ = _session()
    assert session.episode_index == 0
    session.new_episode()
    session.new_episode()
    assert session.episode_index == 2


# ── rollback ─────────────────────────────────────────────────────────────────────


def test_snapshot_then_restore_seeds_the_new_context():
    snapshot_state = {"cookies": [{"name": "sid", "value": "abc"}], "origins": []}
    session, browser, _ = _session(snapshot_state)

    snapshot = session.storage_snapshot()
    assert snapshot == snapshot_state

    session.new_episode(storage_state=snapshot)
    assert browser.contexts[-1].kwargs["storage_state"] == snapshot_state


def test_snapshot_is_empty_without_a_real_context():
    session = BrowserSession(FakePage(), url="http://x/start")
    assert session.storage_snapshot() == {}


# ── new page wiring ──────────────────────────────────────────────────────────────


def test_new_episode_reapplies_the_action_timeout_and_navigates():
    session, browser, _ = _session()
    session.new_episode()

    page = browser.contexts[-1].pages[-1]
    assert page.timeout == 4321, "a fresh page must keep the fail-fast timeout"
    assert page.gotos == ["http://x/start"]


def test_new_episode_can_retarget_the_url():
    session, browser, _ = _session()
    session.new_episode(url="http://x/other")
    assert browser.contexts[-1].pages[-1].gotos == ["http://x/other"]


def test_new_episode_without_owner_only_renavigates():
    page = FakePage()
    session = BrowserSession(page, url="http://x/start")

    assert session.new_episode() is False
    assert page.gotos == ["http://x/start"]
