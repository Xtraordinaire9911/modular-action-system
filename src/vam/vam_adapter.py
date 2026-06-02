"""VAM adapter — System-2 supervisor wrapping HuggingFace VLMs.

The adapter is only invoked when System 1 fails (confidence < 0.9,
postcondition failure, or repeated backend failure). It must never be
called as the default execution path.

Supported models: Qwen2-VL (7B), CogAgent (18B), ShowUI (2B).
If transformers/torch are not installed, the adapter reports itself
unavailable so the router selects DOM or WoT instead.

The VAM is expected to return a VisualGroundingResult (selecting a mark_id)
or an EpistemicProbingAction (e.g. "refresh_page", "repoll_sensor").
It must NOT output raw (x, y) coordinates.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from src.contracts.types import ExecutionResult, SkillCall
from src.perception.som_parser import VisualGroundingResult, VisualMark, select_mark
from src.vam.vam_payload import VAMRecoveryPayload

try:
    from transformers import AutoProcessor, AutoModelForVision2Seq  # type: ignore
    import torch  # type: ignore

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    _TRANSFORMERS_AVAILABLE = False


@dataclass
class EpistemicProbingAction:
    """A System-2 instruction to resolve sensory uncertainty before acting."""

    action: str
    reason: str
    target: str | None = None


_SYSTEM2_PROMPT_TEMPLATE = """System 1 failed. Do not guess coordinates.
Failed skill: {skill_id}
Failure reason: {failure_reason}
Available marks: {mark_ids}

Choose one of:
A) Select a mark_id from the list to retry grounding.
B) Propose an Epistemic Probing Action: {{"probe": "<refresh_page|repoll_sensor>", "reason": "<why>"}}

Respond with valid JSON only."""


class VamAdapter:
    """Thin wrapper over a HuggingFace VLM used only for System-2 recovery."""

    def __init__(self, model_name: str = "Qwen2-VL", device: str = "cpu") -> None:
        self._model_name = model_name
        self._device = device
        self._model: Any = None
        self._processor: Any = None
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            self._available = _TRANSFORMERS_AVAILABLE
        return self._available

    def load(self) -> None:
        if not _TRANSFORMERS_AVAILABLE:
            return
        self._processor = AutoProcessor.from_pretrained(self._model_name)
        self._model = AutoModelForVision2Seq.from_pretrained(self._model_name).to(self._device)

    # ------------------------------------------------------------------
    # Main entry point called by the recovery manager
    # ------------------------------------------------------------------

    def recover(
        self,
        payload: VAMRecoveryPayload,
        marks: list[VisualMark],
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
        """Attempt to resolve a System-1 failure.

        In real usage calls the VLM. In mock/test usage falls back to
        select_mark() with the failed skill's label hint so unit tests
        exercise the recovery path without a GPU.
        """
        if not self.is_available() or self._model is None:
            return self._mock_recover(payload, marks)
        return self._vlm_recover(payload, marks)

    def _mock_recover(
        self,
        payload: VAMRecoveryPayload,
        marks: list[VisualMark],
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
        """Deterministic fallback used in tests and CPU-only environments."""
        if payload.failure_reason == "visual_confidence_low":
            label_hint = payload.failed_skill.skill_id.replace("_", " ")
            result = select_mark(marks, label_hint)
            if result:
                return result
        return EpistemicProbingAction(
            action="refresh_page",
            reason=f"could not ground '{payload.failed_skill.skill_id}': {payload.failure_reason}",
        )

    def _vlm_recover(
        self,
        payload: VAMRecoveryPayload,
        marks: list[VisualMark],
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
        mark_ids = [m.mark_id for m in marks]
        prompt = _SYSTEM2_PROMPT_TEMPLATE.format(
            skill_id=payload.failed_skill.skill_id,
            failure_reason=payload.failure_reason,
            mark_ids=mark_ids,
        )
        try:
            inputs = self._processor(text=prompt, return_tensors="pt").to(self._device)
            output_ids = self._model.generate(**inputs, max_new_tokens=128)
            raw = self._processor.decode(output_ids[0], skip_special_tokens=True)
            return self._parse_vlm_output(raw, marks)
        except Exception:
            return None

    def _parse_vlm_output(
        self,
        raw: str,
        marks: list[VisualMark],
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
        try:
            start = raw.index("{")
            data = json.loads(raw[start:])
        except (ValueError, json.JSONDecodeError):
            return None

        if "probe" in data:
            return EpistemicProbingAction(
                action=data["probe"],
                reason=data.get("reason", ""),
            )

        mark_id = data.get("mark_id")
        if mark_id:
            match = next((m for m in marks if m.mark_id == mark_id), None)
            if match:
                return VisualGroundingResult(
                    mark_id=match.mark_id,
                    label=match.label,
                    bbox=match.bbox.as_list(),
                    confidence=match.confidence,
                )
        return None
