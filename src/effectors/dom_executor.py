"""DOM executor — Playwright-backed System-1 reflex for the React booking UI.

Uses cached CSS selectors for deterministic, low-latency execution.
Falls back gracefully when a selector is not found so the recovery
cascade can reroute to the visual backend.
"""

from __future__ import annotations

import time
from typing import Any

from src.contracts.types import ExecutionResult, Observation, SkillCall
from src.effectors.executor_base import ExecutorBase
from src.perception.dom_transducer import parse_html, PageAffordanceModel

# Lazy import of Playwright so the module loads without it in CI
try:
    from playwright.async_api import async_playwright, Browser, Page  # type: ignore

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


_SKILL_TO_DOM_STEPS: dict[str, list[dict[str, Any]]] = {
    "confirm_booking": [
        {"action": "navigate", "target": "/"},
        {"action": "fill", "selector": "input[name='room']", "param": "room"},
        {"action": "fill", "selector": "input[name='time']", "param": "time"},
        {"action": "click", "selector": "#book-room"},
        {"action": "wait_selector", "selector": ".booking-confirmed"},
    ],
    "set_temperature": [
        {"action": "navigate", "target": "/thermostat"},
        {"action": "fill", "selector": "input[name='target']", "param": "target"},
        {"action": "click", "selector": "#apply-temperature"},
    ],
    "set_lighting": [
        {"action": "navigate", "target": "/lighting"},
        {"action": "fill", "selector": "input[name='brightness']", "param": "brightness"},
        {"action": "click", "selector": "#apply-brightness"},
    ],
}


class DomExecutor(ExecutorBase):
    """Execute skills through the React booking dashboard via Playwright."""

    def __init__(self, base_url: str = "http://localhost:5000") -> None:
        self._base_url = base_url.rstrip("/")
        self._browser: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------
    # Lifecycle (used by the controller; not required for unit tests)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not _PLAYWRIGHT_AVAILABLE:
            return
        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(5000)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()

    # ------------------------------------------------------------------
    # ExecutorBase interface
    # ------------------------------------------------------------------

    async def probe_availability(self) -> bool:
        if not _PLAYWRIGHT_AVAILABLE:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(self._base_url)
                return resp.status_code < 500
        except Exception:
            return False

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        t0 = time.monotonic()
        steps = _SKILL_TO_DOM_STEPS.get(skill_call.skill_id)
        if steps is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"no DOM steps defined for skill '{skill_call.skill_id}'",
            )

        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason="Playwright not available or executor not started",
            )

        try:
            for step in steps:
                await self._run_step(step, skill_call.params)
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=True,
                latency_ms=latency,
                confidence=1.0,
            )
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=latency,
                confidence=0.0,
                failure_reason=str(exc),
            )

    async def _run_step(self, step: dict[str, Any], params: dict[str, Any]) -> None:
        page = self._page
        action = step["action"]
        if action == "navigate":
            await page.goto(self._base_url + step["target"])
        elif action == "fill":
            value = str(params.get(step["param"], ""))
            await page.fill(step["selector"], value)
        elif action == "click":
            await page.click(step["selector"])
        elif action == "wait_selector":
            await page.wait_for_selector(step["selector"])
        else:
            raise ValueError(f"unknown DOM step action: {action!r}")

    # ------------------------------------------------------------------
    # Read-only helper for affordance extraction
    # ------------------------------------------------------------------

    async def get_page_affordances(self) -> PageAffordanceModel | None:
        """Fetch the current page HTML and return a PageAffordanceModel."""
        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return None
        html = await self._page.content()
        url = self._page.url
        return parse_html(html, page_id=url)
