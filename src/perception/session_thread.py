"""Let the async runtime drive a synchronous browser session.

Playwright's synchronous API installs an event loop on the thread that uses it,
so `asyncio.run(...)` on that same thread raises "cannot be called from a
running event loop". The async runtime therefore cannot be started once a
browser is open, and moving the coroutine to a worker thread does not help
either: Playwright's sync objects belong to the thread that created them and
raise `TargetClosedError` when touched from another.

That is not hypothetical. It is why `scripts/run_agent_on_env.py --planner
runtime` has never run - it launches a sync session and then calls
`asyncio.run`, which fails immediately on the first invocation.

The fix is to put the browser on a thread of its own and marshal every call to
it. The runtime keeps its event loop on the main thread; the session keeps its
loop on the worker; each call is a queue round trip. The round trip blocks the
caller, which is correct here - the runtime drives one episode at a time and has
nothing else to do while the page is being read.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable


class SessionThread:
    """Owns a browser session on a dedicated thread and runs callables there.

    ``factory`` is called on the worker thread, so the session is created where
    it will be used. Everything the session touches must go through ``call``.
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._requests: queue.Queue[tuple[Callable[[Any], Any], queue.Queue[Any]] | None] = queue.Queue()
        self._ready: queue.Queue[BaseException | None] = queue.Queue()
        self._session: Any = None
        self._thread = threading.Thread(target=self._serve, name="browser-session", daemon=True)
        self._thread.start()
        error = self._ready.get()
        if error is not None:
            raise error

    def _serve(self) -> None:
        try:
            self._session = self._factory()
        except BaseException as exc:  # the caller needs the real failure, not a hang
            self._ready.put(exc)
            return
        self._ready.put(None)
        while True:
            item = self._requests.get()
            if item is None:
                break
            work, reply = item
            try:
                reply.put(("ok", work(self._session)))
            except BaseException as exc:
                reply.put(("error", exc))
        try:
            self._session.close()
        except Exception:
            pass

    def call(self, work: Callable[[Any], Any]) -> Any:
        """Run ``work(session)`` on the browser thread and return its result."""
        reply: queue.Queue[Any] = queue.Queue()
        self._requests.put((work, reply))
        status, value = reply.get()
        if status == "error":
            raise value
        return value

    def close(self) -> None:
        self._requests.put(None)
        self._thread.join(timeout=30)


class ThreadedSession:
    """A session-shaped object whose calls run on a :class:`SessionThread`.

    Only the methods the benchmark adapters actually use are forwarded. Adding
    one that is not here would silently do nothing, so unknown attributes raise
    rather than being proxied blindly.
    """

    def __init__(self, worker: SessionThread) -> None:
        self._worker = worker

    def open(self, url: str) -> None:
        self._worker.call(lambda s: s.open(url))

    def state(self, *, page_id: str = "page", captured_at_ms: int = 0) -> Any:
        return self._worker.call(lambda s: s.state(page_id=page_id, captured_at_ms=captured_at_ms))

    def text_content(self, selector: str) -> str | None:
        return self._worker.call(lambda s: s.text_content(selector))

    def click(self, selector: str) -> None:
        self._worker.call(lambda s: s.click(selector))

    def fill(self, selector: str, value: str) -> None:
        self._worker.call(lambda s: s.fill(selector, value))

    def click_xy(self, x: int, y: int) -> None:
        self._worker.call(lambda s: s.click_xy(x, y))

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        return self._worker.call(lambda s: s.evaluate(expression, arg))

    def content_html(self) -> str:
        return self._worker.call(lambda s: s._page.content())

    def screenshot(self, path: str | None = None) -> bytes:
        return self._worker.call(lambda s: s.screenshot(path))

    def screenshot_element(self, selector: str) -> bytes:
        return self._worker.call(lambda s: s.screenshot_element(selector))

    def close(self) -> None:
        self._worker.close()


__all__ = ["SessionThread", "ThreadedSession"]
