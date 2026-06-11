"""Visual executor — clicks/types at a Set-of-Marks target (System-2 fallback).

The visual path is *not* the default. It runs only when the recovery cascade
routes to it (DOM selector failed, layout shifted, etc.). It acts on a
``VisualGroundingResult`` (a mark the VAM selected) by clicking the bbox centre
— never a coordinate the VLM hallucinated. The pointer device is injected for
unit-testing without a browser.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.contracts.types import Affordance
from src.effectors.base import ExecutorBase
from src.perception.som_parser import VisualGroundingResult


class PointerLike(Protocol):
    """Minimal coordinate-level pointer/keyboard surface (Playwright mouse/keyboard)."""

    def click_xy(self, x: int, y: int) -> Any: ...
    def type_text(self, text: str) -> Any: ...


class VisualExecutor(ExecutorBase):
    backend = "visual"

    def __init__(self, pointer: PointerLike) -> None:
        self._pointer = pointer

    def _run(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        center = affordance.locator.get("center")
        if center is None:
            bbox = affordance.locator.get("bbox")
            if not bbox:
                raise ValueError("visual affordance missing center/bbox")
            x, y, w, h = bbox
            center = [x + w // 2, y + h // 2]
        cx, cy = int(center[0]), int(center[1])
        self._pointer.click_xy(cx, cy)
        if affordance.action in ("type", "select") and value is not None:
            self._pointer.type_text(str(value))
            return {"mark_id": affordance.locator.get("mark_id"), "clicked_xy": [cx, cy], "typed": value}
        return {"mark_id": affordance.locator.get("mark_id"), "clicked_xy": [cx, cy]}

    def execute_grounding(self, grounding: VisualGroundingResult, *, value: Any | None = None) -> Any:
        """Convenience: build a transient VISUAL affordance from a grounding result."""
        aff = Affordance(
            id=f"vis_{grounding.mark_id}",
            source="VISUAL",
            type="button",
            label=grounding.label,
            action="type" if value is not None else "click",
            locator={"mark_id": grounding.mark_id, "bbox": grounding.bbox, "center": list(grounding.center)},
            confidence=grounding.confidence,
        )
        return self.execute(aff, value=value)
