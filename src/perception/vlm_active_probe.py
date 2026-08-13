"""Use fresh screenshot evidence as a generic Runtime active-perception probe."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.contracts.types import Observation, ObservedAssertion
from src.perception.vlm_observer import VisualJudgement, VlmObserver
from src.runtime.cognitive_map import CognitiveMap, Conflict

ScreenshotCapture = Callable[[], bytes | Awaitable[bytes]]


class VlmActivePerceptionProbe:
    """Ask a configured VLM which currently disputed value is visible.

    The probe is domain-independent: candidates come from the conflict itself,
    not from a fixture name or expected answer. A missing, unsure, ambiguous,
    or contradictory model result contributes no assertion, so the Runtime
    keeps the conflict blocked.
    """

    def __init__(self, observer: VlmObserver, capture: ScreenshotCapture) -> None:
        self.observer = observer
        self.capture = capture
        self.judgements: list[VisualJudgement] = []

    async def observe(
        self,
        conflicts: list[Conflict],
        cognitive_map: CognitiveMap,
        original_observation: Observation,
    ) -> Observation | None:
        _ = (cognitive_map, original_observation)
        captured = self.capture()
        image_png = await captured if inspect.isawaitable(captured) else captured
        if not image_png:
            return None

        assertions: list[ObservedAssertion] = []
        for conflict in conflicts:
            assertion = self._resolve_conflict(conflict, image_png)
            if assertion is not None:
                assertions.append(assertion)
        return Observation(screenshot=image_png, assertions=assertions) if assertions else None

    def _resolve_conflict(self, conflict: Conflict, image_png: bytes) -> ObservedAssertion | None:
        candidates = _unique_values(conflict.values)
        accepted: list[tuple[float, str, Any, VisualJudgement]] = []
        for candidate in candidates:
            question = (
                f"For visible state {conflict.entity_id}.{conflict.attribute}, "
                f"is the displayed value {candidate!r}?"
            )
            judgement = self.observer.look(image_png, question, region=conflict.entity_id)
            self.judgements.append(judgement)
            if judgement.usable and judgement.answer:
                accepted.append((judgement.confidence, repr(candidate), candidate, judgement))

        accepted.sort(key=lambda row: (row[0], row[1]), reverse=True)
        if not accepted:
            return None
        if len(accepted) > 1 and accepted[0][0] == accepted[1][0]:
            return None
        confidence, _, value, judgement = accepted[0]
        return ObservedAssertion(
            entity_id=conflict.entity_id,
            attribute=conflict.attribute,
            value=value,
            source="visual",
            confidence=confidence,
            timestamp_ms=int(time.time() * 1000),
            provenance={
                "model": judgement.model,
                "screenshot_sha256": judgement.screenshot_sha256,
                "question": judgement.question,
                "evidence": judgement.evidence,
                "active_perception": True,
            },
        )


def _unique_values(values: dict[str, Any]) -> list[Any]:
    unique: dict[str, Any] = {}
    for value in values.values():
        unique.setdefault(repr(value), value)
    return [unique[key] for key in sorted(unique)]


__all__ = ["VlmActivePerceptionProbe"]
