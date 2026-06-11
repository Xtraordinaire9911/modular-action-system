"""Visual executor — screenshot-driven System-1 reflex using SoM marks.

Normal path (System 1): if a cached mark exists for the target label, execute
click(center) directly via Playwright without invoking the VAM.

Fallback path (System 2 trigger): if no cached mark exists or the click
confidence is below the threshold, return a failure with
failure_reason="visual_confidence_low" so the recovery manager can
escalate to the VAM adapter.
"""

from __future__ import annotations

import time
from typing import Any

from src.contracts.types import ExecutionResult, Observation, SkillCall
from src.effectors.executor_base import ExecutorBase
from src.perception.som_parser import VisualMark, select_mark

try:
    from playwright.async_api import Page  # type: ignore

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_CONFIDENCE_THRESHOLD = 0.9

_SKILL_TO_LABEL: dict[str, str] = {
    "confirm_booking": "Book Room",
    "turn_on_projector": "Projector On",
    "set_temperature": "Apply Temperature",
    "set_lighting": "Apply Brightness",
}


class VisualExecutor(ExecutorBase):
    """Execute skills by clicking cached visual marks on screenshots."""

    def __init__(
        self,
        page: Any = None,
        confidence_threshold: float = _CONFIDENCE_THRESHOLD,
    ) -> None:
        self._page = page
        self._threshold = confidence_threshold
        self._mark_cache: dict[str, list[VisualMark]] = {}

    def update_marks(self, skill_id: str, marks: list[VisualMark]) -> None:
        """Cache the current SoM marks for a skill so System-1 can reuse them."""
        self._mark_cache[skill_id] = marks

    async def probe_availability(self) -> bool:
        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            return False
        try:
            await self._page.evaluate("1 + 1")
            return True
        except Exception:
            return False

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        t0 = time.monotonic()
        label = _SKILL_TO_LABEL.get(skill_call.skill_id)
        if label is None:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="visual",
                success=False,
                latency_ms=latency,
                confidence=0.0,
                failure_reason=f"no visual label mapping for skill '{skill_call.skill_id}'",
            )

        marks = self._mark_cache.get(skill_call.skill_id, [])
        result = select_mark(marks, label)

        if result is None or result.confidence < self._threshold:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="visual",
                success=False,
                latency_ms=latency,
                confidence=result.confidence if result else 0.0,
                failure_reason="visual_confidence_low",
            )

        if not _PLAYWRIGHT_AVAILABLE or self._page is None:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="visual",
                success=False,
                latency_ms=latency,
                confidence=result.confidence,
                failure_reason="Playwright not available or executor not started",
            )

        try:
            cx, cy = result.bbox[0] + (result.bbox[2] - result.bbox[0]) // 2, result.bbox[1] + (
                result.bbox[3] - result.bbox[1]
            ) // 2
            await self._page.mouse.click(cx, cy)
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="visual",
                success=True,
                latency_ms=latency,
                confidence=result.confidence,
            )
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="visual",
                success=False,
                latency_ms=latency,
                confidence=0.0,
                failure_reason=str(exc),
            )
