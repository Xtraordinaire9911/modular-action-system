"""Isolated Playwright browser session — the web analogue of PiP isolation.

The advisor's "PiP session isolation for CUA" (§9.2) means the agent must drive
the GUI inside a sandbox that cannot interfere with the host or other runs. For
a web target this is an **isolated Playwright browser context** (incognito-like:
its own cookies, storage, and cache), one per task, inside the Docker network.

``BrowserSession`` is the single perception+action surface over that context:

  * ``state()``  → runs the DOM Transducer on the live page → PageAffordanceModel
  * ``screenshot()`` → bytes/file for the Set-of-Marks pipeline
  * ``click``/``fill`` (selector-level, for the DOM executor — PageLike protocol)
  * ``click_xy``/``type_text`` (coordinate-level, for the visual executor — PointerLike)

Playwright is imported lazily and the underlying page is injectable, so this
module unit-tests with a fake page and never needs a real browser in CI.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol, cast

from src.perception.dom_transducer import DomTransducer
from src.perception.page_affordance_model import PageAffordanceModel

_AGENT_SCREENSHOT_STYLE = """
#__cua_cursor, #__cua_cap, #__cua_badge, .__cua_dot,
[data-agent-overlay='true'], [data-runtime-overlay='true'] {
    display: none !important;
}
.__cua_hl {
    outline: none !important;
    box-shadow: none !important;
}
"""


class _PageDriver(Protocol):
    def goto(self, url: str) -> Any: ...
    def content(self) -> str: ...
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def screenshot(self, **kwargs: Any) -> bytes: ...


class BrowserSession:
    """One isolated browser context. Construct via :meth:`launch` for a real
    browser, or pass a ``page`` driver directly for tests."""

    def __init__(self, page: _PageDriver, *, url: str = "", _owner: Any = None) -> None:
        self._page = page
        self._url = url
        self._owner = _owner  # (playwright, browser) kept alive until close()
        self._transducer = DomTransducer()
        self.context_id = f"browser-context-{uuid.uuid4().hex[:12]}"

    # ── lifecycle ────────────────────────────────────────────────────────────
    @classmethod
    def launch(cls, url: str, *, headless: bool = True, action_timeout_ms: int = 8000) -> "BrowserSession":
        """Start Playwright, open a fresh isolated context, and navigate."""
        from playwright.sync_api import sync_playwright  # lazy

        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
        )  # ← isolation boundary (PiP analogue)
        page = context.new_page()
        # Cap action waits so a mistargeted click fails fast instead of hanging
        # the default 30s (e.g. clicking a non-actionable element).
        page.set_default_timeout(action_timeout_ms)
        last_error: Exception | None = None
        for _ in range(5):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=10_000)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(1.0)
        if last_error is not None:
            context.close()
            browser.close()
            pw.stop()
            raise last_error
        return cls(cast(_PageDriver, page), url=url, _owner=(pw, browser, context))

    def open(self, url: str) -> None:
        self._url = url
        self._page.goto(url)

    def reset(self) -> None:
        """Return the context to its initial page — cheap per-trial reset."""
        if self._url:
            self._page.goto(self._url)

    def close(self) -> None:
        if self._owner is not None:
            pw, browser, context = self._owner
            context.close()
            browser.close()
            pw.stop()
            self._owner = None

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── perception ───────────────────────────────────────────────────────────
    def state(self, *, page_id: str = "page", captured_at_ms: int = 0) -> PageAffordanceModel:
        """Perceive the current page as a Page Affordance Model."""
        current_url = str(getattr(self._page, "url", "") or self._url)
        return self._transducer.transduce(
            self._page.content(), page_id=page_id, url=current_url, captured_at_ms=captured_at_ms
        )

    def screenshot(self, path: str | None = None) -> bytes:
        last_error: Exception | None = None
        waiter = getattr(self._page, "wait_for_load_state", None)
        for _ in range(3):
            try:
                if waiter is not None:
                    try:
                        waiter("networkidle", timeout=2_000)
                    except Exception:
                        waiter("domcontentloaded", timeout=2_000)
                kwargs: dict[str, Any] = {
                    "full_page": True,
                    "animations": "disabled",
                    "style": _AGENT_SCREENSHOT_STYLE,
                }
                if path:
                    kwargs["path"] = path
                return self._page.screenshot(**kwargs)
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)
        if last_error is not None:
            raise last_error
        raise RuntimeError("screenshot failed without an exception")

    def evaluate(self, expression: str, arg: Any | None = None) -> Any:
        evaluator = getattr(self._page, "evaluate", None)
        if evaluator is None:
            return None
        return evaluator(expression, arg) if arg is not None else evaluator(expression)

    # ── action: PageLike (DOM executor) ──────────────────────────────────────
    def click(self, selector: str) -> None:
        self._page.click(selector)

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value)

    def text_content(self, selector: str) -> str | None:
        getter = getattr(self._page, "text_content", None)
        return getter(selector) if getter else None

    # ── action: PointerLike (visual executor) ────────────────────────────────
    def click_xy(self, x: int, y: int) -> None:
        mouse = getattr(self._page, "mouse", None)
        if mouse is not None:
            mouse.click(x, y)
        else:  # fake/headless driver in tests
            self._page.click_xy(x, y)  # type: ignore[attr-defined]

    def type_text(self, text: str) -> None:
        keyboard = getattr(self._page, "keyboard", None)
        if keyboard is not None:
            keyboard.type(text)
        else:
            self._page.type_text(text)  # type: ignore[attr-defined]
