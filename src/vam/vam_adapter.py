"""VAM adapter for System-2 recovery over Set-of-Marks targets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from src.contracts.types import Affordance
from src.perception.som_parser import VisualGroundingResult, VisualMark, select_mark
from src.vam.vam_payload import VAMRecoveryPayload

try:
    from transformers import AutoModelForVision2Seq, AutoProcessor  # type: ignore

    _TRANSFORMERS_AVAILABLE = True
except ImportError:
    _TRANSFORMERS_AVAILABLE = False

ModelFn = Callable[[dict[str, Any]], str]


@dataclass
class EpistemicProbingAction:
    action: str
    reason: str
    target: str | None = None


_SYSTEM2_PROMPT_TEMPLATE = """System 1 failed. Do not guess coordinates.
Failed skill: {skill_id}
Failure reason: {failure_reason}
Available marks: {mark_ids}

Choose JSON: {{"mark_id": "<id>"}} or {{"probe": "<refresh_page|repoll_sensor>", "reason": "<why>"}}."""


class VamAdapter:
    def __init__(
        self,
        *,
        model: ModelFn | None = None,
        confidence_threshold: float = 0.9,
        model_name: str = "Qwen2-VL",
        device: str = "cpu",
    ) -> None:
        self._selection_model = model
        self._tau = confidence_threshold
        self._model_name = model_name
        self._device = device
        self._model: Any = None
        self._processor: Any = None
        self._available: bool | None = None

    def should_invoke(
        self,
        *,
        confidence: float = 1.0,
        postcondition_passed: bool = True,
        selector_failed: bool = False,
        backend_available: bool = True,
    ) -> bool:
        return confidence < self._tau or not postcondition_passed or selector_failed or not backend_available

    def is_available(self) -> bool:
        if self._selection_model is not None:
            return True
        if self._available is None:
            self._available = _TRANSFORMERS_AVAILABLE
        return self._available

    def load(self) -> None:
        if not _TRANSFORMERS_AVAILABLE:
            return
        self._processor = AutoProcessor.from_pretrained(self._model_name)
        self._model = AutoModelForVision2Seq.from_pretrained(self._model_name).to(self._device)
        self._available = True

    def recover(
        self,
        payload: VAMRecoveryPayload,
        marks: list[VisualMark] | None = None,
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
        """Return a mark grounding or an epistemic probe without raw coordinate output."""
        visual_affordances = [a for a in payload.candidate_affordances if a.source == "VISUAL"]
        if self._selection_model is not None or visual_affordances:
            return self._recover_from_affordances(payload, visual_affordances)
        marks = marks or []
        if self._model is not None and _TRANSFORMERS_AVAILABLE:
            return self._vlm_recover(payload, marks)
        return self._mock_recover(payload, marks)

    def _recover_from_affordances(
        self,
        payload: VAMRecoveryPayload,
        visual_affordances: list[Affordance],
    ) -> VisualGroundingResult | None:
        if not visual_affordances:
            return None
        if self._selection_model is not None:
            mark_id = self._selection_model(payload.to_dict())
        else:
            mark_id = self._heuristic_select(visual_affordances, payload.failed_skill.skill_id)
        chosen = next((a for a in visual_affordances if a.locator.get("mark_id") == mark_id), None)
        if chosen is None:
            return None
        bbox = [int(v) for v in chosen.locator["bbox"]]
        center = chosen.locator.get("center", [bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2])
        return VisualGroundingResult(
            mark_id=str(mark_id),
            label=chosen.label,
            bbox=bbox,
            confidence=chosen.confidence,
            center=(int(center[0]), int(center[1])),
        )

    @staticmethod
    def _heuristic_select(visual_affordances: list[Affordance], skill_id: str) -> str:
        tokens = {token for token in skill_id.replace("_", " ").lower().split() if len(token) > 2}

        def score(affordance: Affordance) -> tuple[int, float]:
            label_tokens = set(affordance.label.lower().split())
            return len(tokens & label_tokens), affordance.confidence

        return str(max(visual_affordances, key=score).locator["mark_id"])

    def _mock_recover(
        self,
        payload: VAMRecoveryPayload,
        marks: list[VisualMark],
    ) -> VisualGroundingResult | EpistemicProbingAction | None:
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
        mark_ids = [mark.mark_id for mark in marks]
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
            data = json.loads(raw[raw.index("{") :])
        except (ValueError, json.JSONDecodeError):
            return None
        if "probe" in data:
            return EpistemicProbingAction(action=data["probe"], reason=data.get("reason", ""))
        mark_id = data.get("mark_id")
        match = next((mark for mark in marks if mark.mark_id == mark_id), None)
        if match is None:
            return None
        return VisualGroundingResult(
            mark_id=match.mark_id,
            label=match.label,
            bbox=match.bbox.as_list(),
            confidence=match.confidence,
            center=match.bbox.center,
        )
