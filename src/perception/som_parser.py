"""Set-of-Marks (SoM) visual grounding layer.

Annotates a screenshot with bounding boxes and numeric mark IDs before
handing it to the VAM. The VAM then selects a mark_id rather than
hallucinating raw (x,y) coordinates, which keeps GUI interaction
deterministic and auditable.

In production the bounding boxes come from an OmniParser-style detection
model. This module provides the data structures, the overlay renderer
(OpenCV optional — falls back to PIL), and a mock detector for tests.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoundingBox:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.x + self.w, self.y + self.h]


@dataclass
class VisualMark:
    mark_id: str
    label: str
    bbox: BoundingBox
    confidence: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualGroundingResult:
    mark_id: str
    label: str
    bbox: list[int]
    confidence: float


class SomParseError(ValueError):
    pass


def annotate_screenshot(
    screenshot_bytes: bytes,
    marks: list[VisualMark],
) -> bytes:
    """Draw bounding boxes and numeric IDs onto *screenshot_bytes*.

    Returns PNG bytes. Tries cv2 first, falls back to PIL.
    If neither is installed, returns the original bytes unchanged
    (acceptable in CI environments without GUI deps).
    """
    try:
        return _annotate_cv2(screenshot_bytes, marks)
    except ImportError:
        pass
    try:
        return _annotate_pil(screenshot_bytes, marks)
    except ImportError:
        return screenshot_bytes


def _annotate_cv2(screenshot_bytes: bytes, marks: list[VisualMark]) -> bytes:
    import cv2  # type: ignore
    import numpy as np

    img_array = np.frombuffer(screenshot_bytes, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        raise SomParseError("cv2 could not decode screenshot bytes")

    for mark in marks:
        bb = mark.bbox
        cv2.rectangle(img, (bb.x, bb.y), (bb.x + bb.w, bb.y + bb.h), (0, 200, 0), 2)
        cv2.putText(
            img,
            mark.mark_id,
            (bb.x + 2, bb.y + 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 200, 0),
            1,
            cv2.LINE_AA,
        )

    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise SomParseError("cv2 could not encode annotated image as PNG")
    return buf.tobytes()


def _annotate_pil(screenshot_bytes: bytes, marks: list[VisualMark]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    for mark in marks:
        bb = mark.bbox
        draw.rectangle([bb.x, bb.y, bb.x + bb.w, bb.y + bb.h], outline=(0, 200, 0), width=2)
        draw.text((bb.x + 2, bb.y + 2), mark.mark_id, fill=(0, 200, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def select_mark(marks: list[VisualMark], label_hint: str) -> VisualGroundingResult | None:
    """Return the highest-confidence mark whose label contains *label_hint*.

    The VAM calls this after selecting a mark_id to get the final
    VisualGroundingResult that the visual executor uses.
    """
    candidates = [m for m in marks if label_hint.lower() in m.label.lower()]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: m.confidence)
    return VisualGroundingResult(
        mark_id=best.mark_id,
        label=best.label,
        bbox=best.bbox.as_list(),
        confidence=best.confidence,
    )


def marks_from_affordances(affordances: list[Any]) -> list[VisualMark]:
    """Build VisualMark list from DOM affordances that carry bbox information.

    Affordances without a 'bbox' key in their locator are skipped; they have
    no visual representation that the SoM layer can use.
    """
    marks: list[VisualMark] = []
    for idx, aff in enumerate(affordances):
        bbox_data = aff.locator.get("bbox")
        if bbox_data is None:
            continue
        try:
            x, y, x2, y2 = map(int, bbox_data)
        except (TypeError, ValueError) as e:
            raise SomParseError(f"affordance bbox must be [x, y, x2, y2], got: {bbox_data!r}") from e

        x1, x2 = (x, x2) if x <= x2 else (x2, x)
        y1, y2 = (y, y2) if y <= y2 else (y2, y)
        marks.append(
            VisualMark(
                mark_id=f"M{idx:03d}",
                label=aff.label,
                bbox=BoundingBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                confidence=aff.confidence,
            )
        )
    return marks
