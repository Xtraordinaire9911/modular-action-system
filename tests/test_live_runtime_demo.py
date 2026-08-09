"""Tests for live-demo runtime-control adapters without starting Docker."""

from __future__ import annotations

import asyncio

from evaluation import live_runtime_demo
import run_demo
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.effectors.wot_executor import WotExecutor
from src.runtime.continuous_interaction_manager import RuntimeStepResult
from src.runtime.episode import TransitionLedger, TransitionRecord
from src.runtime.state_machine import RuntimeState


class _FakeWotExecutor:
    def __init__(self) -> None:
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="wot",
            success=True,
            latency_ms=3.0,
            confidence=1.0,
            raw_observation_delta={"device_reply": "ok"},
        )


def test_live_wot_adapter_maps_contract_params_and_reobserves_state(monkeypatch):
    fake_wot = _FakeWotExecutor()

    monkeypatch.setattr(
        run_demo,
        "_read_state",
        lambda _control_url: {
            "state": {
                "thermostat": {"targetTemperature": 22, "currentTemperature": 22},
                "lights": {"brightness": 80},
                "projector": {"power": "off"},
            }
        },
    )

    adapter = run_demo._LiveWotSkillExecutor(fake_wot, "http://control", booked=lambda: True)
    result = asyncio.run(adapter.execute(SkillCall("set_temperature", {"room": "A", "target": 22}), Observation()))

    assert fake_wot.calls[0].params["target"] == 22
    assert fake_wot.calls[0].params["targetTemperature"] == 22
    assert result.skill_id == "set_temperature"
    assert result.raw_observation_delta["thermostat"]["target_temperature"] == 22
    assert result.raw_observation_delta["thermostat_service_available"] is True


def test_live_runtime_planning_hints_restore_semantic_input_names():
    affordances = [
        Affordance(
            "dom_input_1",
            "DOM",
            "input",
            "A",
            "type",
            {"selector": "[data-testid='room-input']"},
            0.97,
        ),
        Affordance(
            "dom_input_2",
            "DOM",
            "input",
            "14:00",
            "type",
            {"selector": "[data-testid='time-input']"},
            0.97,
        ),
    ]

    hinted = run_demo._with_live_runtime_planning_hints(affordances)

    assert hinted[0].label == "Room"
    assert hinted[0].locator["skill_id"] == "room"
    assert hinted[1].label == "Time"
    assert hinted[1].locator["skill_id"] == "time"


def test_wot_executor_matches_skill_to_discovered_uuid_affordance_label():
    executor = WotExecutor()
    affordance = Affordance(
        "wot_uuid_setTargetTemperature",
        "WOT",
        "action",
        "setTargetTemperature",
        "invoke",
        {"thing_id": "urn:uuid:thermostat", "href": "http://h/actions/setTargetTemperature", "method": "POST"},
        1.0,
    )
    executor._affordances[affordance.id] = affordance

    assert executor._affordance_for_skill("set_temperature") == affordance


def test_live_wot_adapter_handles_readiness_as_reobserved_verification(monkeypatch):
    monkeypatch.setattr(
        run_demo,
        "_read_state",
        lambda _control_url: {
            "state": {
                "thermostat": {"targetTemperature": 22, "currentTemperature": 22},
                "lights": {"brightness": 40},
                "projector": {"power": "on"},
            }
        },
    )

    adapter = run_demo._LiveWotSkillExecutor(_FakeWotExecutor(), "http://control", booked=lambda: True)
    result = asyncio.run(adapter.execute(SkillCall("verify_readiness", {"room": "A"}), Observation()))

    assert result.success is True
    assert result.raw_observation_delta["readiness"]["ready"] is True


def test_runtime_trace_entry_records_cim_controller_for_live_steps():
    result = RuntimeStepResult(
        RuntimeState.COMPLETED,
        ExecutionResult("set_lighting", "wot", True, 1.0, 1.0),
        selected_backend="wot",
        reason="skill completed",
    )

    entry = run_demo._runtime_trace_entry(
        skill_id="set_lighting",
        controller="ContinuousInteractionManager.run_skill",
        observation_source="control plane state -> normalized Observation -> CognitiveMap",
        result=result,
    )

    assert entry["controller"] == "ContinuousInteractionManager.run_skill"
    assert entry["runtime_step"]["state"] == "completed"
    assert entry["runtime_step"]["selected_backend"] == "wot"


def test_system1_latency_report_links_warmup_repeat_and_amortized_runtime():
    ledger = TransitionLedger()
    warmup = RuntimeStepResult(
        RuntimeState.COMPLETED,
        ExecutionResult("set_temperature_reflex", "wot", True, 30.0, 1.0),
        episode_id="ep-warmup",
        final_outcome_verified=True,
        system1_cache_hit=False,
        system1_fast_path=False,
        system1_routing_latency_ms=4.0,
    )
    repeat = RuntimeStepResult(
        RuntimeState.COMPLETED,
        ExecutionResult("set_temperature_reflex", "wot", True, 8.0, 1.0),
        episode_id="ep-repeat",
        final_outcome_verified=True,
        system1_cache_hit=True,
        system1_fast_path=True,
        system1_routing_latency_ms=0.5,
    )
    ledger.record(
        TransitionRecord(
            task_id="live_system1_reflex",
            episode_id="ep-warmup",
            transition_id="ep-warmup:transition-0001",
            step=1,
            state_id_before="s0",
            state_id_after="s1",
            skill_id="set_temperature_reflex",
            affordance_key="WOT:thermostat.set_target_temperature",
            backend="wot",
            params={"target": 21},
            success=True,
            execution_success=True,
            postcondition_passed=True,
            latency_ms=30.0,
            attempt=1,
            observation_delta={},
        )
    )
    ledger.record(
        TransitionRecord(
            task_id="live_system1_reflex",
            episode_id="ep-repeat",
            transition_id="ep-repeat:transition-0001",
            step=1,
            state_id_before="s1",
            state_id_after="s2",
            skill_id="set_temperature_reflex",
            affordance_key="WOT:thermostat.set_target_temperature",
            backend="wot",
            params={"target": 21},
            success=True,
            execution_success=True,
            postcondition_passed=True,
            latency_ms=8.0,
            attempt=1,
            observation_delta={},
        )
    )

    report = live_runtime_demo._system1_latency_report(warmup, repeat, ledger)

    assert report["episode_ids"] == ["ep-warmup", "ep-repeat"]
    assert report["cache_hit_rate"] == 0.5
    assert report["warmup"]["cache_hit"] is False
    assert report["repeat"]["cache_hit"] is True
    assert report["repeat"]["fast_path"] is True
    assert report["warmup"]["transition_latency_ms"] == 30.0
    assert report["repeat"]["transition_latency_ms"] == 8.0
    assert report["total_transition_latency_ms"] == 38.0
    assert report["amortized_transition_latency_ms"] == 19.0
