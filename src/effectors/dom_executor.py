"""DOM executor — Playwright-backed System-1 reflex for the web booking UI.

Drives a browser *page* using the CSS selector that the DOM Transducer put in
``affordance.locator["selector"]``. The page object is injected (it can be a
Playwright ``Page`` or our ``BrowserSession``) so this executor unit-tests with
a fake page and never requires a real browser at import time.

Primitive map:  click → page.click,  type → page.fill,  select → page.select_option,
                submit → page.click (the submit control).
"""

from __future__ import annotations

from typing import Any, Protocol

from src.contracts.types import Affordance
from src.effectors.base import ExecutorBase


class PageLike(Protocol):
    """Minimal surface we need from a Playwright Page / BrowserSession."""

    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def text_content(self, selector: str) -> str | None: ...


class DomExecutor(ExecutorBase):
    backend = "dom"

    def __init__(self, page: PageLike) -> None:
        self._page = page

    def _run(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        selector = affordance.locator.get("selector")
        if not selector:
            raise ValueError("DOM affordance missing CSS selector")
        if affordance.state.get("enabled") is False:
            raise RuntimeError(f"element {selector} is disabled")

        action = affordance.action
        if action == "type":
            self._page.fill(selector, "" if value is None else str(value))
            return {"selector": selector, "typed": value}
        if action == "select":
            select_option = getattr(self._page, "select_option", None)
            if select_option is None:
                raise RuntimeError("page has no select_option capability")
            select_option(selector, str(value))
            return {"selector": selector, "selected": value}
        # click / submit
        self._page.click(selector)
        return {"selector": selector, "clicked": True}
