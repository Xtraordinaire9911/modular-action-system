"""Set-of-Marks (SoM) visual grounding — OmniParser-inspired (advisor §8).

The assessment warns that a naive VLM cannot reliably predict raw ``(x, y)``
coordinates for zero-shot GUI interaction. The SoM pattern fixes this:

    screenshot + detected interactable regions
        ──▶  overlay a numbered box on every region
        ──▶  expose marks as VISUAL Affordances (mark_id, bbox, label)
        ──▶  the VAM / System-2 supervisor selects a *mark_id*, never a coordinate

This keeps grounding controllable and auditable. To stay dependency-free and
fully unit-testable (no Pillow/OpenCV in requirements), the overlay is rendered
as an SVG string layered over the screenshot reference rather than a rasterised
PNG. The structured ``VisualAffordance`` list is the contract the executor and
evaluator consume; the SVG is a human-facing demo artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance

# A detected region the upstream detector hands us. ``bbox`` is [x, y, w, h].
Region = dict[str, Any]


@dataclass
class VisualGroundingResult:
    """The single target the VAM/System-2 selected, by mark id (not coordinates)."""

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


def _center(bbox: list[int]) -> tuple[int, int]:
    x, y, w, h = bbox
    return (x + w // 2, y + h // 2)


class SetOfMarksParser:
    """Annotate detected regions with numeric marks and emit VISUAL affordances."""

    def __init__(self, *, min_confidence: float = 0.0) -> None:
        self._min_confidence = min_confidence

    def parse(self, regions: list[Region], *, screenshot_ref: str = "") -> list[Affordance]:
        """Convert detector regions into stable, numbered VISUAL affordances."""
        affordances: list[Affordance] = []
        index = 0
        for region in regions:
            conf = float(region.get("confidence", 1.0))
            if conf < self._min_confidence:
                continue
            bbox = [int(v) for v in region["bbox"]]
            mark_id = f"M{index}"
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
                        "center": list(_center(bbox)),
                        "screenshot_ref": screenshot_ref,
                    },
                    confidence=conf,
                    state={"ocr": region.get("ocr", "")},
                    safety_level="low",
                )
            )
            index += 1
        return affordances

    def select(self, affordances: list[Affordance], mark_id: str) -> VisualGroundingResult:
        """Resolve a VAM-chosen mark id into a concrete, executable target."""
        for aff in affordances:
            if aff.locator.get("mark_id") == mark_id:
                bbox = aff.locator["bbox"]
                return VisualGroundingResult(
                    mark_id=mark_id,
                    label=aff.label,
                    bbox=bbox,
                    confidence=aff.confidence,
                    center=tuple(aff.locator.get("center", _center(bbox))),  # type: ignore[arg-type]
                )
        raise KeyError(f"mark_id {mark_id!r} not present in current Set-of-Marks")

    def render_overlay_svg(self, affordances: list[Affordance], *, width: int = 1280, height: int = 800) -> str:
        """Render a numbered-box overlay as SVG (demo artifact, no binary deps)."""
        boxes: list[str] = []
        for aff in affordances:
            mark_id = aff.locator.get("mark_id", "?")
            x, y, w, h = aff.locator["bbox"]
            boxes.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                f'fill="none" stroke="#e2001a" stroke-width="2"/>'
                f'<text x="{x + 2}" y="{y + 14}" fill="#e2001a" '
                f'font-family="monospace" font-size="13">{mark_id}</text>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            + "".join(boxes)
            + "</svg>"
        )


@dataclass
class SoMResult:
    """Bundle of everything a SoM pass produces, for trace/demo export."""

    affordances: list[Affordance]
    overlay_svg: str
    screenshot_ref: str = ""
    marks: list[dict[str, Any]] = field(default_factory=list)
