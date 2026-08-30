from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import src.demos.final_presentation as final_demo
from scripts.run_supervised_smartroom_demo import ControlledBookingObstructionExecutor
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.demos.final_presentation import (
    CapabilitySnapshot,
    FinalDemoConfig,
    build_chapters,
    coverage_report,
    resolve_model_mode,
    run_final_demo,
    validate_chapter,
)
from src.planner.model_recovery_planner import AgentChoice, PlanningMode


def _capabilities(*, models: bool = False, recorded: bool = True) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        browser=True,
        browser_detail="chromium installed",
        smart_room=True,
        smart_room_detail="all services reachable",
        text_model="text-demo" if models else "",
        vision_model="vision-demo" if models else "",
        recorded_model_evidence=recorded,
    )


def _config(tmp_path: Path, **changes: object) -> FinalDemoConfig:
    values: dict[str, object] = {
        "output_dir": tmp_path / "run",
        "headless": True,
        "auto_approve": True,
        "fast": True,
    }
    values.update(changes)
    return FinalDemoConfig(**values)  # type: ignore[arg-type]


def test_auto_model_mode_prefers_live_then_recorded_then_skip() -> None:
    assert resolve_model_mode("auto", _capabilities(models=True)) == "live"
    assert resolve_model_mode("auto", _capabilities(recorded=True)) == "recorded"
    assert resolve_model_mode("auto", _capabilities(recorded=False)) == "skip"
    assert resolve_model_mode("skip", _capabilities(models=True)) == "skip"


def test_presentation_profile_builds_one_story_plus_bounded_extension_chapters(tmp_path: Path) -> None:
    config = _config(tmp_path, profile="presentation", model_mode="recorded")
    chapters = build_chapters(config, _capabilities())

    assert [chapter.chapter_id for chapter in chapters] == [
        "canonical",
        "recovery",
        "models",
        "visual",
        "adaptation",
    ]
    canonical = chapters[0]
    assert "--inject-booking-obstruction" in canonical.command
    assert "--auto-approve" in canonical.command
    assert "--headless" in canonical.command
    assert chapters[2].command == ()
    assert chapters[2].execution_mode == "checked-in recorded model evidence"


def test_complete_profile_adds_live_runtime_system1_laboratory(tmp_path: Path) -> None:
    chapters = build_chapters(
        _config(tmp_path, profile="complete", model_mode="skip"),
        _capabilities(recorded=False),
    )

    assert [chapter.chapter_id for chapter in chapters][:3] == ["canonical", "runtime_lab", "recovery"]
    runtime = next(chapter for chapter in chapters if chapter.chapter_id == "runtime_lab")
    assert runtime.command[1:4] == ("-m", "src.pipeline", "--live-demo")


def test_runtime_lab_cannot_be_silently_selected_from_presentation_profile(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--profile complete"):
        build_chapters(
            _config(tmp_path, profile="presentation", only=("runtime_lab",)),
            _capabilities(),
        )


def test_live_model_chapter_has_explicit_model_requirements_and_run_directory(tmp_path: Path) -> None:
    chapters = build_chapters(
        _config(tmp_path, model_mode="live", canonical_model=True),
        _capabilities(models=True),
    )
    model = next(chapter for chapter in chapters if chapter.chapter_id == "models")
    canonical = next(chapter for chapter in chapters if chapter.chapter_id == "canonical")

    assert model.requirements == ("browser", "smart_room", "text_model", "vision_model")
    assert "--out" in model.command
    assert "--use-model" in canonical.command


class _Session:
    def __init__(self) -> None:
        self.injected: list[str] = []

    async def evaluate(self, expression: str, arg: object = None) -> bool:
        assert "__injectFault" in expression
        self.injected.append(str(arg))
        return True


class _Environment:
    def __init__(self) -> None:
        self.session = _Session()
        self.affordance = Affordance(
            id="dom_confirm",
            source="DOM",
            type="button",
            label="Book Room",
            action="click",
            locator={"stable_key": "booking.confirm"},
            confidence=1.0,
        )

    def find_affordance(self, affordance_id: str) -> Affordance | None:
        return self.affordance if affordance_id == self.affordance.id else None


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=False,
            latency_ms=1.0,
            confidence=1.0,
            failure_reason="click_intercepted",
        )


def test_booking_obstruction_is_occurrence_bound_hidden_and_injected_once() -> None:
    async def scenario() -> tuple[object, object, object]:
        environment = _Environment()
        delegate = _Delegate()
        wrapper = ControlledBookingObstructionExecutor(delegate, environment, settle_s=0)
        call = SkillCall("prepare_and_confirm_room", {"affordance_id": "dom_confirm"})
        first = await wrapper.execute(call, Observation())
        second = await wrapper.execute(call, Observation())
        return environment, delegate, (first, second, wrapper)

    environment, delegate, result = asyncio.run(scenario())
    first, second, wrapper = result
    assert environment.session.injected == ["overlay_obstruction"]
    assert delegate.calls == 2
    assert wrapper.injected
    assert first.metadata["controlled_fault_injected"] == "booking_obstruction"
    assert first.metadata["fault_visible_to_planner"] is False
    assert second.metadata["controlled_fault_injected"] == "booking_obstruction"


def test_agent_choice_serializes_prompt_and_raw_model_reply() -> None:
    choice = AgentChoice(
        mode=PlanningMode.RECOVERY,
        source="llm",
        prompt="mode: recovery\naffordances observed after the failure: M1",
        raw_response='{"affordance_id":"M1","action":"click"}',
    )

    payload = choice.to_dict()
    assert payload["prompt"].startswith("mode: recovery")
    assert payload["raw_response"].startswith("{")


def test_canonical_validator_requires_recovery_surface_intervention_and_restore(tmp_path: Path) -> None:
    config = _config(tmp_path, model_mode="skip")
    spec = next(chapter for chapter in build_chapters(config, _capabilities()) if chapter.chapter_id == "canonical")
    assert spec.output_dir is not None
    spec.output_dir.mkdir(parents=True)
    (spec.output_dir / "episode.json").write_text(
        json.dumps(
            {
                "result": {"verified": True},
                "surfaces_used": ["dom", "wot"],
                "room_state_restored": True,
                "controlled_fault": {"applied": True},
                "agent_planner": {"decisions": [{"mode": "forward"}, {"mode": "recovery"}]},
                "transitions": [
                    {"transition_id": "t-failed", "success": False, "recovery_of_transition_id": ""},
                    {"transition_id": "t-recovery", "success": True, "recovery_of_transition_id": "t-failed"},
                ],
                "interventions": [{"decision": "approve"}],
            }
        ),
        encoding="utf-8",
    )

    valid, checks = validate_chapter(spec)
    assert valid
    assert len(checks) == 8


def test_manifest_survives_and_reports_review_gated_adaptation_only_run(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, model_mode="skip", only=("adaptation",))
    monkeypatch.setattr(final_demo, "strict_preflight", lambda config, capabilities: [])

    def fake_executor(spec, interactive):
        assert not interactive
        assert spec.output_dir is not None
        spec.output_dir.mkdir(parents=True, exist_ok=True)
        (spec.output_dir / "adaptation_report.json").write_text(
            json.dumps({"summary": {"policy_proposals": 1}}), encoding="utf-8"
        )
        (spec.output_dir / "policy_proposals.json").write_text(
            json.dumps(
                {
                    "proposals": [
                        {"safe_to_auto_apply": False, "release_gate": {"approved": False}}
                    ]
                }
            ),
            encoding="utf-8",
        )
        return 0, ""

    code, manifest = run_final_demo(config, capabilities=_capabilities(), executor=fake_executor)

    assert code == 0
    assert manifest["status"] == "passed"
    assert manifest["chapters"][0]["chapter_id"] == "adaptation"
    saved = json.loads((config.output_dir / "presentation_manifest.json").read_text(encoding="utf-8"))
    assert saved["summary"]["required_failures"] == []
    coverage = coverage_report(
        build_chapters(config, _capabilities()),
        [final_demo.ChapterResult("adaptation", "", "passed", "", "", "", "")],
    )
    assert next(item for item in coverage if item["component_id"] == "release_gate")["status"] == "passed"
    assert next(item for item in coverage if item["component_id"] == "intent")["status"] == "not_run"
