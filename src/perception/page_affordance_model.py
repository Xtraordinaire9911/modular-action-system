"""Page Affordance Model (PAM) — the compact, task-relevant view of a page.

The DOM Transduction Pattern maps raw HTML into a small set of
``Affordance`` objects so the rest of the pipeline never has to read raw HTML.
The PAM is the container that carries those affordances plus page-level
provenance (which page, which URL, when it was captured).

Consumed by the Cognitive Map and the backend
router. The ``Affordance`` dataclass itself lives in
``src.contracts.types`` so every component shares one schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance


@dataclass
class PageAffordanceModel:
    """A cleaned, structured snapshot of one page's interactable surface."""

    page_id: str
    url: str
    affordances: list[Affordance] = field(default_factory=list)
    captured_at_ms: int = 0
    raw_node_count: int = 0
    kept_node_count: int = 0

    @property
    def compression_ratio(self) -> float:
        """Fraction of DOM nodes discarded — evidence that we don't bloat context."""
        if self.raw_node_count == 0:
            return 0.0
        return 1.0 - (self.kept_node_count / self.raw_node_count)

    def by_label(self, label: str) -> Affordance | None:
        target = label.strip().lower()
        for aff in self.affordances:
            if aff.label.strip().lower() == target:
                return aff
        return None

    def by_id(self, affordance_id: str) -> Affordance | None:
        return next((a for a in self.affordances if a.id == affordance_id), None)

    def find_by_label(self, text: str) -> Affordance | None:
        text_lower = text.strip().lower()
        for affordance in self.affordances:
            if text_lower in affordance.label.strip().lower():
                return affordance
        return None

    def find_by_selector(self, selector: str) -> Affordance | None:
        for affordance in self.affordances:
            if affordance.locator.get("selector") == selector:
                return affordance
        return None

    def clickable(self) -> list[Affordance]:
        return [a for a in self.affordances if a.action == "click"]

    def inputs(self) -> list[Affordance]:
        return [a for a in self.affordances if a.action in ("type", "select")]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "url": self.url,
            "captured_at_ms": self.captured_at_ms,
            "raw_node_count": self.raw_node_count,
            "kept_node_count": self.kept_node_count,
            "compression_ratio": round(self.compression_ratio, 4),
            "affordances": [
                {
                    "id": a.id,
                    "source": a.source,
                    "type": a.type,
                    "label": a.label,
                    "action": a.action,
                    "locator": a.locator,
                    "confidence": a.confidence,
                    "state": a.state,
                    "safety_level": a.safety_level,
                }
                for a in self.affordances
            ],
        }
