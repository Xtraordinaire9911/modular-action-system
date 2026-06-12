"""Visual executor using Set-of-Marks targets."""

from __future__ import annotations

import time
from typing import Any, Protocol

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.som_parser import VisualGroundingResult, VisualMark, select_mark

try:
    from playwright.async_api import Page  # type: ignore  # noqa: F401

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_CONFIDENCE_THRESHOLD = 0.9
_SKILL_TO_LABEL = {
    "confirm_booking": "Book Room",
    "book_room": "Book Room",
    "turn_on_projector": "Projector On",
    "set_temperature": "Apply Temperature",
    "set_lighting": "Apply Brightness",
}


class PointerLike(Protocol):
    def click_xy(self, x: int, y: int) -> Any: ...
    def type_text(self, text: str) -> Any: ...


class VisualExecutor:
    backend = "visual"

    def __init__(self, pointer: PointerLike | Any = None, confidence_threshold: float = _CONFIDENCE_THRESHOLD) -> None:
        self._pointer = pointer if hasattr(pointer, "click_xy") else None
        self._page = None if self._pointer is not None else pointer
        self._threshold = confidence_threshold
        self._mark_cache: dict[str, list[VisualMark]] = {}

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
        center = affordance.locator.get("center")
        if center is None:
            bbox = affordance.locator.get("bbox")
            if not bbox:
                raise ValueError("visual affordance missing center/bbox")
            x, y, w, h = [int(v) for v in bbox]
            center = [x + w // 2, y + h // 2]
        cx, cy = int(center[0]), int(center[1])
        if self._pointer is None:
            raise RuntimeError("visual executor has no pointer")
        self._pointer.click_xy(cx, cy)
        delta: dict[str, Any] = {"mark_id": affordance.locator.get("mark_id"), "clicked_xy": [cx, cy]}
        if affordance.action in ("type", "select") and value is not None:
            self._pointer.type_text(str(value))
            delta["typed"] = value
        return delta

    def execute_grounding(self, grounding: VisualGroundingResult, *, value: Any | None = None) -> ExecutionResult:
        aff = Affordance(
            id=f"vis_{grounding.mark_id}",
            source="VISUAL",
            type="input" if value is not None else "button",
            label=grounding.label,
            action="type" if value is not None else "click",
            locator={"mark_id": grounding.mark_id, "bbox": grounding.bbox, "center": list(grounding.center)},
            confidence=grounding.confidence,
        )
        return self._execute_affordance(aff, value=value)

    def update_marks(self, skill_id: str, marks: list[VisualMark]) -> None:
        self._mark_cache[skill_id] = marks

    async def probe_availability(self) -> bool:
        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return False
        try:
            page: Any = self._page
            await page.evaluate("1 + 1")
            return True
        except Exception:
            return False

    async def _execute_skill(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        start = time.monotonic()
        label = _SKILL_TO_LABEL.get(skill_call.skill_id)
        if label is None:
            return self._skill_failure(skill_call, start, f"no visual label mapping for skill '{skill_call.skill_id}'")
        result = select_mark(self._mark_cache.get(skill_call.skill_id, []), label)
        if result is None or result.confidence < self._threshold:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=False,
                latency_ms=(time.monotonic() - start) * 1000.0,
                confidence=result.confidence if result else 0.0,
                failure_reason="visual_confidence_low",
            )
        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return self._skill_failure(
                skill_call, start, "Playwright not available or executor not started", result.confidence
            )
        try:
            page: Any = self._page
            cx, cy = result.center or (
                result.bbox[0] + (result.bbox[2] - result.bbox[0]) // 2,
                result.bbox[1] + (result.bbox[3] - result.bbox[1]) // 2,
            )
            await page.mouse.click(cx, cy)
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=True,
                latency_ms=(time.monotonic() - start) * 1000.0,
                confidence=result.confidence,
            )
        except Exception as exc:
            return self._skill_failure(skill_call, start, str(exc), 0.0)

    def _skill_failure(
        self, skill_call: SkillCall, start: float, reason: str, confidence: float = 0.0
    ) -> ExecutionResult:
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=False,
            latency_ms=(time.monotonic() - start) * 1000.0,
            confidence=confidence,
            failure_reason=reason,
        )
