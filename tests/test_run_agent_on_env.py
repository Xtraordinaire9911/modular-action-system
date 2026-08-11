from __future__ import annotations

import asyncio
import threading
from typing import Any

from scripts.run_agent_on_env import _launch_threaded_session, _selector_success_check


class _ScopedTextAdapter:
    def __init__(self, text_by_selector: dict[str, str]) -> None:
        self.text_by_selector = text_by_selector

    def text_content(self, selector: str) -> str:
        return self.text_by_selector.get(selector, "")


class _ThreadRecordingSession:
    def __init__(self) -> None:
        self.created_on = threading.get_ident()
        self.calls: list[int] = []

    def open(self, url: str) -> None:
        _ = url
        self.calls.append(threading.get_ident())

    def screenshot(self, path: str | None = None) -> bytes:
        _ = path
        self.calls.append(threading.get_ident())
        return b"png"

    def close(self) -> None:
        self.calls.append(threading.get_ident())


def test_success_text_is_scoped_to_declared_state_region() -> None:
    check = _selector_success_check("#cart", ["Wireless Headphones"])
    adapter = _ScopedTextAdapter(
        {
            "body": "Wireless Headphones Add to cart",
            "#cart": "Your cart is empty",
        }
    )

    assert not check(adapter)  # type: ignore[arg-type]
    adapter.text_by_selector["#cart"] = "1 × Wireless Headphones"
    assert check(adapter)  # type: ignore[arg-type]


def test_browser_session_is_created_and_used_on_worker_thread() -> None:
    created: list[_ThreadRecordingSession] = []

    def factory(url: str, *, headless: bool) -> Any:
        _ = url, headless
        session = _ThreadRecordingSession()
        created.append(session)
        return session

    main_thread = threading.get_ident()
    session = _launch_threaded_session("http://example.test", headless=True, session_factory=factory)
    try:
        session.open("http://example.test/next")
        assert session.screenshot() == b"png"
        asyncio.run(asyncio.sleep(0))
    finally:
        session.close()

    assert created[0].created_on != main_thread
    assert created[0].calls
    assert set(created[0].calls) == {created[0].created_on}
