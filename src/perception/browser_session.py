"""Isolated Playwright browser session: one browser context per episode.

What this provides is **browser-context isolation** - incognito-like, with its
own cookies, storage and cache - so one run cannot observe or disturb another.

It is deliberately *not* described as Picture-in-Picture. An earlier revision of
this docstring called it "the web analogue of PiP isolation", which the review
identified as a misreading of the referenced paper: PiP means a supervised
picture-in-picture interface, where the agent operates in a visibly separate
session a human can watch and take over. That interface is a distinct piece of
work; conflating the two made a weaker property look like the requested one.

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


_VIEWPORT = {"width": 1280, "height": 800}


def _fresh_context(
    browser: Any,
    storage_state: dict[str, Any] | None = None,
    record_video_dir: str | None = None,
) -> Any:
    """Create a context with the settings every episode must share.

    ``record_video_dir`` records the page itself rather than the desktop, so a
    demo capture contains one window and nothing the recorder happened to have
    open. Playwright writes the file when the context closes.
    """
    kwargs: dict[str, Any] = {"viewport": dict(_VIEWPORT), "device_scale_factor": 1}
    if storage_state:
        kwargs["storage_state"] = storage_state
    if record_video_dir:
        kwargs["record_video_dir"] = record_video_dir
        kwargs["record_video_size"] = dict(_VIEWPORT)
    return browser.new_context(**kwargs)


class BrowserSession:
    """One isolated browser context. Construct via :meth:`launch` for a real
    browser, or pass a ``page`` driver directly for tests."""

    def __init__(
        self,
        page: _PageDriver,
        *,
        url: str = "",
        _owner: Any = None,
        action_timeout_ms: int = 8000,
    ) -> None:
        self._page = page
        self._url = url
        self._owner = _owner  # (playwright, browser) kept alive until close()
        self._transducer = DomTransducer()
        self.context_id = f"browser-context-{uuid.uuid4().hex[:12]}"
        self._action_timeout_ms = action_timeout_ms  # reapplied to each new episode page
        self._episode_index = 0

    # ── lifecycle ────────────────────────────────────────────────────────────
    @classmethod
    def launch(
        cls,
        url: str,
        *,
        headless: bool = True,
        action_timeout_ms: int = 8000,
        record_video_dir: str | None = None,
    ) -> "BrowserSession":
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
        # The isolation boundary: its own cookies, storage and cache.
        context = _fresh_context(browser, record_video_dir=record_video_dir)
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
        return cls(
            cast(_PageDriver, page),
            url=url,
            _owner=(pw, browser, context),
            action_timeout_ms=action_timeout_ms,
        )

    def open(self, url: str) -> None:
        self._url = url
        self._page.goto(url)

    def reset(self) -> None:
        """Return the context to its initial page — cheap per-trial reset.

        This is navigation only. Cookies, localStorage and sessionStorage all
        survive it, so it is *not* an episode boundary: use :meth:`new_episode`
        when the next run must not observe what the previous one wrote.
        """
        if self._url:
            self._page.goto(self._url)

    @property
    def episode_index(self) -> int:
        """How many isolated episodes this session has started (0 = the first)."""
        return self._episode_index

    def storage_snapshot(self) -> dict[str, Any]:
        """Cookies and per-origin storage for the live context.

        Returns ``{}`` when there is no real context (injected page driver) or
        the driver cannot report one, so a caller can distinguish "nothing to
        restore" from "restored an empty state".
        """
        if self._owner is None:
            return {}
        _pw, _browser, context = self._owner
        getter = getattr(context, "storage_state", None)
        if getter is None:
            return {}
        try:
            state = getter()
        except Exception:
            return {}
        return dict(state) if isinstance(state, dict) else {}

    def new_episode(self, *, url: str | None = None, storage_state: dict[str, Any] | None = None) -> bool:
        """Start the next episode in a brand-new browser context.

        Recreating the context — not re-navigating — is the real isolation
        boundary: it is what drops cookies, localStorage, sessionStorage and
        cache, so a later episode cannot observe state an earlier one left
        behind. Pass ``storage_state`` (from :meth:`storage_snapshot`) to seed
        the fresh context, which is how a verified rollback is performed.

        Returns False when there is no real context to recreate (injected page
        driver in tests); the session is then only re-navigated.
        """
        target = url or self._url
        if self._owner is None:
            if target:
                self.open(target)
            return False
        pw, browser, context = self._owner
        context.close()  # drop the old boundary before opening the new one
        context = _fresh_context(browser, storage_state)
        page = context.new_page()
        setter = getattr(page, "set_default_timeout", None)
        if setter is not None:
            setter(self._action_timeout_ms)
        self._page = cast(_PageDriver, page)
        self._owner = (pw, browser, context)
        self._episode_index += 1
        if target:
            self.open(target)
        return True

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
