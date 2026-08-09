"""Layer 3: choose which Set-of-Marks target to act on, given a goal.

This is the layer the Set-of-Marks representation exists to serve. The point of
numbering every interactive element is that a model can then answer with an
identifier - "M002" - instead of guessing pixel coordinates, which is the whole
argument of the screenshot-parsing line of work the project follows.

The project had the numbering and no model. Marks were produced faithfully and
then handed to substring matching, which is the plumbing of a model-driven agent
running without the model. This closes that gap.

The same provenance discipline as the intent layer applies, for the same reason:

* ``Selection.source`` is ``"llm"`` only when a model actually chose. The
  deterministic scorer is reported as ``"heuristic"`` and is never dressed up as
  a decision.
* the model's own stated reason is kept, so the choice can be argued with rather
  than merely observed. A heuristic result carries the scoring breakdown in the
  same field, so both paths explain themselves in the same place.
* prompt, reply, choice and latency go to a JSONL ledger, because a selector
  nobody can inspect cannot be evaluated against the deterministic baseline.

Text-only by design: marks carry their labels, so a text model is sufficient and
the screenshot is not required. That is precisely the reduction Set-of-Marks
buys, and it keeps the layer cheap enough to run on every step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.demos.deliberation import deliberate
from src.planner.intent_planner import LLMClient

DEFAULT_LEDGER = Path("artifacts/mark_selector/calls.jsonl")

_SYSTEM_PROMPT = """You are choosing which element on a screen an agent should \
act on next.

You will be given a goal and a numbered list of the interactive elements that \
were detected on the page. Choose the single element that best advances the \
goal.

Reply with JSON only:
  mark_id     the identifier of your choice, exactly as listed
  reason      one sentence explaining why that element and not the others
  confidence  number between 0 and 1

If no listed element can advance the goal, reply with mark_id "none" and say \
why in reason. Never invent an identifier that is not in the list.
"""


@dataclass
class Selection:
    """Which mark was chosen, by what, and on what grounds."""

    mark_id: str | None
    mark: Any | None = None
    source: str = "heuristic"  # "llm" | "heuristic" | "none"
    model: str = ""
    reason: str = ""
    confidence: float = 0.0
    considered: int = 0
    latency_ms: float = 0.0
    raw_response: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.mark is not None

    @property
    def is_model_derived(self) -> bool:
        return self.source == "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark_id": self.mark_id,
            "source": self.source,
            "model": self.model,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "considered": self.considered,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
        }


def describe_marks(marks: Iterable[Any]) -> str:
    """The candidate list exactly as the model will see it."""
    lines = []
    for mark in marks:
        action = str(getattr(mark, "extra", {}).get("action", "click"))
        box = getattr(mark, "bbox", None)
        where = f" at ({box.center[0]},{box.center[1]})" if box is not None else ""
        lines.append(f"{mark.mark_id}: {mark.label!r} [{action}]{where}")
    return "\n".join(lines)


@dataclass
class MarkSelector:
    """Picks a Set-of-Marks target, with a model when one is configured."""

    client: LLMClient | None = None
    ledger_path: Path = field(default_factory=lambda: DEFAULT_LEDGER)

    def select(self, marks: Iterable[Any], goal: str) -> Selection:
        candidates = list(marks)
        if not candidates:
            return Selection(mark_id=None, source="none", reason="nothing interactive was perceived")

        if self.client is None:
            return self._heuristic(candidates, goal, reason_prefix="no model configured")

        listing = describe_marks(candidates)
        user = f"Goal: {goal}\n\nDetected elements:\n{listing}"
        started = time.monotonic()
        try:
            raw = self.client.complete(_SYSTEM_PROMPT, user)
            selection = self._parse(raw, candidates)
        except Exception as exc:
            selection = self._heuristic(candidates, goal, reason_prefix="model call failed")
            selection.error = f"{type(exc).__name__}: {exc}"
        selection.latency_ms = (time.monotonic() - started) * 1000.0
        selection.considered = len(candidates)
        if selection.is_model_derived:
            selection.model = getattr(self.client, "name", "")
        self._log(goal, listing, selection)
        return selection

    def _parse(self, raw: str, candidates: list[Any]) -> Selection:
        from src.planner.intent_planner import _extract_json

        payload = _extract_json(raw)
        chosen_id = str(payload.get("mark_id", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        confidence = float(payload.get("confidence", 0.0) or 0.0)

        if chosen_id.lower() in ("none", ""):
            return Selection(
                mark_id=None, source="none", reason=reason or "model declined", confidence=confidence, raw_response=raw
            )

        # A model naming an element that was never offered is a hallucination,
        # and acting on it would be worse than not acting. Rejected outright.
        match = next((m for m in candidates if m.mark_id == chosen_id), None)
        if match is None:
            return Selection(
                mark_id=None,
                source="none",
                reason=reason,
                raw_response=raw,
                error=f"model chose {chosen_id!r}, which was not offered",
            )
        return Selection(
            mark_id=chosen_id, mark=match, source="llm", reason=reason, confidence=confidence, raw_response=raw
        )

    def _heuristic(self, candidates: list[Any], goal: str, *, reason_prefix: str) -> Selection:
        """Fall back to the deterministic scorer, labelled as what it is."""
        decision = deliberate(candidates, goal)
        if decision.chosen is None:
            return Selection(
                mark_id=None,
                source="none",
                considered=decision.considered,
                reason=f"{reason_prefix}; deterministic scoring found no eligible element",
            )
        return Selection(
            mark_id=decision.chosen.mark_id,
            mark=decision.chosen_mark,
            source="heuristic",
            considered=decision.considered,
            confidence=min(1.0, decision.chosen.score / 100.0),
            reason=f"{reason_prefix}; " + decision.explain(top=3).replace("\n", " | "),
        )

    def _log(self, goal: str, listing: str, selection: Selection) -> None:
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": datetime.now(timezone.utc).isoformat(),
                "goal": goal,
                "candidates": listing,
                "raw_response": selection.raw_response,
                **selection.to_dict(),
            }
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass


__all__ = ["MarkSelector", "Selection", "describe_marks"]
