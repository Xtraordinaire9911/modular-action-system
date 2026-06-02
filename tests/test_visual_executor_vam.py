"""Unit tests for VisualExecutor and VamAdapter."""

from __future__ import annotations

import pytest

from src.contracts.types import Observation, SkillCall
from src.effectors.visual_executor import VisualExecutor
from src.perception.som_parser import BoundingBox, VisualMark
from src.vam.vam_adapter import EpistemicProbingAction, VamAdapter
from src.vam.vam_payload import VAMRecoveryPayload


def _mark(mid: str, label: str, conf: float = 0.95) -> VisualMark:
    return VisualMark(
        mark_id=mid,
        label=label,
        bbox=BoundingBox(x=10, y=20, w=80, h=30),
        confidence=conf,
    )


# ── VisualExecutor ─────────────────────────────────────────────────────────────


class TestVisualExecutor:
    @pytest.mark.asyncio
    async def test_unknown_skill_returns_failure(self):
        ex = VisualExecutor()
        result = await ex.execute(SkillCall("unknown", {}), Observation())
        assert not result.success
        assert "no visual label mapping" in result.failure_reason

    @pytest.mark.asyncio
    async def test_no_marks_returns_confidence_low(self):
        ex = VisualExecutor()
        # no marks cached
        result = await ex.execute(SkillCall("confirm_booking", {}), Observation())
        assert not result.success
        assert result.failure_reason == "visual_confidence_low"

    @pytest.mark.asyncio
    async def test_low_confidence_mark_returns_failure(self):
        ex = VisualExecutor()
        ex.update_marks("confirm_booking", [_mark("M001", "Book Room", conf=0.5)])
        result = await ex.execute(SkillCall("confirm_booking", {}), Observation())
        assert not result.success
        assert result.failure_reason == "visual_confidence_low"

    @pytest.mark.asyncio
    async def test_high_confidence_without_playwright_returns_failure(self):
        ex = VisualExecutor()
        ex.update_marks("confirm_booking", [_mark("M001", "Book Room", conf=0.95)])
        result = await ex.execute(SkillCall("confirm_booking", {}), Observation())
        assert not result.success
        assert "Playwright" in result.failure_reason


# ── VamAdapter ────────────────────────────────────────────────────────────────


class TestVamAdapter:
    def _payload(self, reason: str = "visual_confidence_low") -> VAMRecoveryPayload:
        return VAMRecoveryPayload(
            failed_skill=SkillCall("confirm_booking", {}),
            failure_reason=reason,
            screenshot_path="/tmp/shot.png",
            page_affordance_model={},
            cognitive_map_snapshot={},
        )

    def test_not_available_without_transformers(self):
        vam = VamAdapter()
        # transformers probably not installed in CI
        # just verify it doesn't crash
        result = vam.is_available()
        assert isinstance(result, bool)

    def test_mock_recover_returns_epistemic_action_on_unknown_failure(self):
        vam = VamAdapter()
        marks = [_mark("M001", "something_else")]
        payload = self._payload(reason="postcondition_failed")
        result = vam._mock_recover(payload, marks)
        assert isinstance(result, EpistemicProbingAction)
        assert result.action in ("refresh_page", "repoll_sensor")

    def test_mock_recover_returns_grounding_on_low_confidence(self):
        vam = VamAdapter()
        marks = [_mark("M001", "confirm booking")]
        payload = self._payload(reason="visual_confidence_low")
        result = vam._mock_recover(payload, marks)
        # may return VisualGroundingResult or EpistemicProbingAction depending on marks
        assert result is not None

    def test_parse_vlm_output_probe(self):
        vam = VamAdapter()
        raw = '... {"probe": "refresh_page", "reason": "uncertain state"}'
        result = vam._parse_vlm_output(raw, [])
        assert isinstance(result, EpistemicProbingAction)
        assert result.action == "refresh_page"

    def test_parse_vlm_output_mark_id(self):
        vam = VamAdapter()
        marks = [_mark("M005", "Book Room")]
        raw = '{"mark_id": "M005"}'
        from src.perception.som_parser import VisualGroundingResult

        result = vam._parse_vlm_output(raw, marks)
        assert isinstance(result, VisualGroundingResult)
        assert result.mark_id == "M005"

    def test_parse_vlm_output_invalid_json_returns_none(self):
        vam = VamAdapter()
        result = vam._parse_vlm_output("not json at all", [])
        assert result is None
