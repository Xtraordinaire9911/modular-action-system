"""DOM executor for both cached System-1 affordances and skill-level flows."""

from __future__ import annotations

import time
from typing import Any, Protocol

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.dom_transducer import DomTransducer
from src.perception.page_affordance_model import PageAffordanceModel

try:
    from playwright.async_api import async_playwright  # type: ignore

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


class PageLike(Protocol):
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def text_content(self, selector: str) -> str | None: ...


_SKILL_TO_DOM_STEPS: dict[str, list[dict[str, Any]]] = {
    "confirm_booking": [
        {"action": "navigate", "target": "/"},
        {"action": "fill", "selector": "[data-testid='room-input']", "param": "room"},
        {"action": "fill", "selector": "[data-testid='time-input']", "param": "time"},
        {"action": "click", "selector": "[data-testid='book-room-button']"},
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


class DomExecutor:
    backend = "dom"

    def __init__(self, page: PageLike | None = None, base_url: str = "http://localhost:3000") -> None:
        if isinstance(page, str):
            base_url, page = page, None
        self._page: Any = page
        self._browser: Any = None
        self._playwright: Any = None
        self._base_url = base_url.rstrip("/")

    def execute(
        self,
        target: Affordance | SkillCall,
        observation: Observation | None = None,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult | Any:
        if isinstance(target, SkillCall):
            return self._execute_skill(target, observation or Observation())
        return self._execute_affordance(target, value=value, skill_id=skill_id)

    def _execute_affordance(self, affordance: Affordance, *, value: Any | None, skill_id: str = "") -> ExecutionResult:
        start = time.perf_counter()
        try:
            delta = self._run_affordance(affordance, value)
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=True,
                latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
                confidence=affordance.confidence,
                raw_observation_delta=delta,
            )
        except Exception as exc:
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=False,
                latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
                confidence=affordance.confidence,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

    def _run_affordance(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        selector = affordance.locator.get("selector")
        if not selector:
            raise ValueError("DOM affordance missing CSS selector")
        if affordance.state.get("enabled") is False:
            raise RuntimeError(f"element {selector} is disabled")
        if self._page is None:
            raise RuntimeError("DOM executor has no page/session")

        if affordance.action == "type":
            self._page.fill(selector, "" if value is None else str(value))
            return {"selector": selector, "typed": value}
        if affordance.action == "select":
            select_option = getattr(self._page, "select_option", None)
            if select_option is None:
                raise RuntimeError("page has no select_option capability")
            select_option(selector, str(value))
            return {"selector": selector, "selected": value}
        self._page.click(selector)
        return {"selector": selector, "clicked": True}

    async def start(self) -> None:
        if not _PLAYWRIGHT_AVAILABLE or self._page is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(5000)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def probe_availability(self) -> bool:
        if not _PLAYWRIGHT_AVAILABLE:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(self._base_url)
            return response.status_code < 500
        except Exception:
            return False

    async def _execute_skill(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        start = time.monotonic()
        steps = _SKILL_TO_DOM_STEPS.get(skill_call.skill_id)
        if steps is None:
            return self._skill_failure(skill_call, start, f"no DOM steps defined for skill '{skill_call.skill_id}'")
        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return self._skill_failure(skill_call, start, "Playwright not available or executor not started")
        try:
            for step in steps:
                await self._run_step(step, skill_call.params)
            delta = self._build_skill_delta(skill_call)
            if skill_call.skill_id == "confirm_booking":
                status_text = (await self._page.text_content("[data-testid='booking-status']")) or ""
                expected_room = str(skill_call.params.get("room", "")).strip().upper()
                expected_time = str(skill_call.params.get("time", "")).strip()
                if (
                    "booked:" not in status_text.lower()
                    or expected_room not in status_text
                    or expected_time not in status_text
                ):
                    return self._skill_failure(skill_call, start, "postcondition_mismatch")
                delta["booking_status"] = "confirmed"
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=True,
                latency_ms=(time.monotonic() - start) * 1000.0,
                confidence=1.0,
                raw_observation_delta=delta,
            )
        except Exception as exc:
            return self._skill_failure(skill_call, start, str(exc))

    def _build_skill_delta(self, skill_call: SkillCall) -> dict[str, Any]:
        if skill_call.skill_id == "confirm_booking":
            return {
                "booking_status": "confirmed",
                "bookings": {
                    str(skill_call.params.get("room", ""))
                    .strip()
                    .upper(): {
                        "booked": True,
                        "time": skill_call.params.get("time"),
                    }
                },
            }
        if skill_call.skill_id == "set_temperature":
            return {"thermostat": {"target_temperature": skill_call.params.get("target")}}
        if skill_call.skill_id == "set_lighting":
            return {"lighting": {"brightness": skill_call.params.get("brightness")}}
        return {}

    def _skill_failure(self, skill_call: SkillCall, start: float, reason: str) -> ExecutionResult:
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=False,
            latency_ms=(time.monotonic() - start) * 1000.0,
            confidence=0.0,
            failure_reason=reason,
        )

    async def _run_step(self, step: dict[str, Any], params: dict[str, Any]) -> None:
        action = step["action"]
        if action == "navigate":
            await self._page.goto(self._base_url + step["target"])
        elif action == "fill":
            await self._page.fill(step["selector"], str(params.get(step["param"], "")))
        elif action == "click":
            await self._page.click(step["selector"])
        elif action == "wait_selector":
            await self._page.wait_for_selector(step["selector"])
        else:
            raise ValueError(f"unknown DOM step action: {action!r}")

    async def get_page_affordances(self) -> PageAffordanceModel | None:
        if self._page is None:
            return None
        content = self._page.content()
        html = await content if hasattr(content, "__await__") else content
        url = getattr(self._page, "url", "")
        return DomTransducer().transduce(html, page_id=url or "page", url=url)
