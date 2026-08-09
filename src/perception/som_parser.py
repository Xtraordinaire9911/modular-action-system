"""Set-of-Marks visual grounding layer.

The VAM/System-2 path should select mark IDs, not hallucinated coordinates.
This module converts detected regions or bbox-carrying affordances into visual
marks, resolves selected marks, and can render either binary screenshot
annotations or a dependency-free SVG overlay for demos and CI.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance

Region = dict[str, Any]


@dataclass
class BoundingBox:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_xywh(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]

    def as_list(self) -> list[int]:
        """Return legacy xyxy format used by earlier visual tests."""
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
    center: tuple[int, int] = (0, 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark_id": self.mark_id,
            "label": self.label,
            "bbox": self.bbox,
            "center": list(self.center),
            "confidence": self.confidence,
        }


class SomParseError(ValueError):
    pass


def _center_xywh(bbox: list[int]) -> tuple[int, int]:
    x, y, w, h = bbox
    return x + w // 2, y + h // 2


class SetOfMarksParser:
    """Annotate detected regions with numeric marks and emit VISUAL affordances."""

    def __init__(self, *, min_confidence: float = 0.0) -> None:
        self._min_confidence = min_confidence

    def parse(self, regions: list[Region], *, screenshot_ref: str = "") -> list[Affordance]:
        affordances: list[Affordance] = []
        mark_index = 0
        for region in regions:
            confidence = float(region.get("confidence", 1.0))
            if confidence < self._min_confidence:
                continue
            bbox = [int(v) for v in region["bbox"]]
            if len(bbox) != 4:
                raise SomParseError(f"region bbox must be [x, y, w, h], got: {bbox!r}")
            mark_id = f"M{mark_index}"
            label = str(region.get("label", "")).strip() or mark_id
            action = str(region.get("action", "click"))
            affordances.append(
                Affordance(
                    id=f"vis_{mark_id}",
                    source="VISUAL",
                    type="input" if action in ("type", "select") else "button",
                    label=label,
                    action=action,
                    locator={
                        "mark_id": mark_id,
                        "bbox": bbox,
                        "center": list(_center_xywh(bbox)),
                        "screenshot_ref": screenshot_ref,
                    },
                    confidence=confidence,
                    state={"ocr": region.get("ocr", "")},
                    safety_level="low",
                )
            )
            mark_index += 1
        return affordances

    def select(self, affordances: list[Affordance], mark_id: str) -> VisualGroundingResult:
        for affordance in affordances:
            if affordance.locator.get("mark_id") == mark_id:
                bbox = [int(v) for v in affordance.locator["bbox"]]
                return VisualGroundingResult(
                    mark_id=mark_id,
                    label=affordance.label,
                    bbox=bbox,
                    confidence=affordance.confidence,
                    center=tuple(affordance.locator.get("center", _center_xywh(bbox))),  # type: ignore[arg-type]
                )
        raise KeyError(f"mark_id {mark_id!r} not present in current Set-of-Marks")

    def render_overlay_svg(self, affordances: list[Affordance], *, width: int = 1280, height: int = 800) -> str:
        boxes: list[str] = []
        for affordance in affordances:
            mark_id = affordance.locator.get("mark_id", "?")
            x, y, w, h = [int(v) for v in affordance.locator["bbox"]]
            boxes.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" '
                f'stroke="#e2001a" stroke-width="2"/>'
                f'<text x="{x + 2}" y="{y + 14}" fill="#e2001a" '
                f'font-family="monospace" font-size="13">{mark_id}</text>'
            )
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">' + "".join(boxes) + "</svg>"


@dataclass
class SoMResult:
    affordances: list[Affordance]
    overlay_svg: str
    screenshot_ref: str = ""
    marks: list[dict[str, Any]] = field(default_factory=list)


def annotate_screenshot(screenshot_bytes: bytes, marks: list[VisualMark]) -> bytes:
    """Draw bounding boxes and mark IDs onto PNG bytes; fall back gracefully in CI."""
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
        cv2.putText(img, mark.mark_id, (bb.x + 2, bb.y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise SomParseError("cv2 could not encode annotated image as PNG")
    return buf.tobytes()


def _annotate_pil(screenshot_bytes: bytes, marks: list[VisualMark]) -> bytes:
    from PIL import Image, ImageDraw  # type: ignore

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
    candidates = [m for m in marks if label_hint.lower() in m.label.lower()]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: m.confidence)
    return VisualGroundingResult(
        mark_id=best.mark_id,
        label=best.label,
        bbox=best.bbox.as_list(),
        confidence=best.confidence,
        center=best.bbox.center,
    )


def marks_from_affordances(affordances: list[Any]) -> list[VisualMark]:
    """Build VisualMarks from affordances with locator['bbox'].

    Accepts both xywh boxes from the new DOM/SoM pipeline and xyxy boxes from
    older tests. Ambiguous boxes are treated as xywh, which matches current
    runtime contracts.
    """
    marks: list[VisualMark] = []
    for idx, affordance in enumerate(affordances):
        bbox_data = affordance.locator.get("bbox")
        if bbox_data is None:
            continue
        try:
            a, b, c, d = [int(v) for v in bbox_data]
        except (TypeError, ValueError) as exc:
            raise SomParseError(f"affordance bbox must have four numeric values, got: {bbox_data!r}") from exc
        if c > a and d > b and affordance.locator.get("bbox_format") == "xyxy":
            x, y, w, h = a, b, c - a, d - b
        else:
            x, y, w, h = a, b, c, d
        marks.append(
            VisualMark(
                mark_id=f"M{idx:03d}",
                label=affordance.label,
                bbox=BoundingBox(x=x, y=y, w=w, h=h),
                confidence=affordance.confidence,
                # Keep a way back to the affordance this mark came from, so a
                # later probe can question the element itself rather than only
                # the rectangle. Acting still goes through the bbox.
                extra={"selector": str(affordance.locator.get("selector", ""))},
            )
        )
    return marks
