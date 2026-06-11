"""VAM adapter — System-2 supervisor interface (advisor §7.3, §8).

Member B owns the *adapter*; Member C decides *when* to call it. The adapter:

  * exposes :meth:`should_invoke` encoding the strict wake conditions, and
  * :meth:`recover` which, given a :class:`VAMRecoveryPayload`, returns a
    Set-of-Marks selection (a ``mark_id`` via :class:`VisualGroundingResult`).

A real vision-language model (ShowUI / Qwen2-VL / CogAgent) can be plugged in
via the ``model`` callable. The default is a deterministic, offline fallback
that picks the highest-confidence visual candidate whose label best matches the
failed skill — so the pipeline runs end-to-end in CI without GPUs, and the demo
can swap in a frozen VLM later.
"""

from __future__ import annotations

from typing import Callable

from src.contracts.types import Affordance
from src.perception.som_parser import VisualGroundingResult
from src.vam.vam_payload import VAMRecoveryPayload

# model(payload_dict) -> mark_id
ModelFn = Callable[[dict], str]


class VamAdapter:
    def __init__(self, *, model: ModelFn | None = None, confidence_threshold: float = 0.9) -> None:
        self._model = model
        self._tau = confidence_threshold

    def should_invoke(
        self,
        *,
        confidence: float = 1.0,
        postcondition_passed: bool = True,
        selector_failed: bool = False,
        backend_available: bool = True,
    ) -> bool:
        return (
            confidence < self._tau
            or not postcondition_passed
            or selector_failed
            or not backend_available
        )

    def recover(self, payload: VAMRecoveryPayload) -> VisualGroundingResult | None:
        """Select a recovery target as a Set-of-Marks mark id."""
        visual = [a for a in payload.candidate_affordances if a.source == "VISUAL"]
        if not visual:
            return None

        mark_id: str | None = None
        if self._model is not None:
            mark_id = self._model(payload.to_dict())
        else:
            mark_id = self._heuristic_select(visual, payload.failed_skill.skill_id)

        chosen = next((a for a in visual if a.locator.get("mark_id") == mark_id), None)
        if chosen is None:
            return None
        bbox = chosen.locator["bbox"]
        center = chosen.locator.get("center", [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2])
        return VisualGroundingResult(
            mark_id=mark_id,  # type: ignore[arg-type]
            label=chosen.label,
            bbox=bbox,
            confidence=chosen.confidence,
            center=(int(center[0]), int(center[1])),
        )

    @staticmethod
    def _heuristic_select(visual: list[Affordance], skill_id: str) -> str:
        """Offline fallback: prefer a label-token match, else highest confidence."""
        tokens = {t for t in skill_id.replace("_", " ").lower().split() if len(t) > 2}

        def score(a: Affordance) -> tuple[int, float]:
            label_tokens = set(a.label.lower().split())
            overlap = len(tokens & label_tokens)
            return (overlap, a.confidence)

        best = max(visual, key=score)
        return str(best.locator["mark_id"])
