"""Offline fixture-driven evaluation harness for Member A tasks.

This module runs every task fixture through ``ContinuousInteractionManager`` with
fully deterministic in-process executors. It is designed for CI reproducibility
and emits per-task scoring plus aggregated metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evaluation.metrics_aggregator import EvaluationDataset, RecoveryCase, TaskOutcome, aggregate_metrics
from src.contracts.types import ExecutionResult, Observation, SkillCall
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult
from src.runtime.state_machine import RuntimeState
from src.skill_library import (
    expected_skill_calls,
    get_task_fixture,
    load_failure_profiles,
    load_skill_library,
    load_task_fixtures,
)


@dataclass
class FixtureStepResult:
    skill_id: str
    success: bool
    attempts: int
    selected_backend: str
    recovery_tier: int
    reason: str


@dataclass
class FixtureTaskEvaluation:
    task_id: str
    task_success: bool
    skill_sequence_match: bool
    recovery_tier_match: bool
    expected_recovery_tier: int
    achieved_recovery_tier: int
    expected_skill_sequence: list[str]
    executed_skill_sequence: list[str]
    final_state: dict[str, Any]
    expected_final_state: dict[str, Any]
    steps: list[FixtureStepResult]


class _DeterministicExecutor:
    def __init__(self, backend: str, fail_once: dict[tuple[str, str], str]) -> None:
        self.backend = backend
        self._fail_once = fail_once

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        failure_key = (skill_call.skill_id, self.backend)
        if failure_key in self._fail_once:
            reason = self._fail_once.pop(failure_key)
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=False,
                latency_ms=10.0,
                confidence=1.0,
                failure_reason=reason,
                raw_observation_delta={},
            )

        delta = self._delta_for_skill(skill_call, observation)
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=True,
            latency_ms=10.0,
            confidence=1.0,
            raw_observation_delta=delta,
        )

    def _delta_for_skill(self, skill_call: SkillCall, observation: Observation) -> dict[str, Any]:
        if skill_call.skill_id == "confirm_booking":
            return {"booking_status": "confirmed", "booking_confirmed": True}
        if skill_call.skill_id == "turn_on_projector":
            return {"projector_A": {"power": "on"}}
        if skill_call.skill_id == "set_temperature":
            target = int(skill_call.params["target"])
            return {"thermostat_A": {"targetTemperature": target}}
        if skill_call.skill_id == "set_lighting":
            brightness = int(skill_call.params["brightness"])
            return {"lights": {"brightness": brightness}}
        if skill_call.skill_id == "verify_readiness":
            return {"readiness": {"ready": _compute_readiness(observation.device_states)}}
        return {}


def _canonical_device_states(task_initial: dict[str, Any]) -> dict[str, Any]:
    target = int(task_initial.get("target_temperature_wot", task_initial.get("target_temperature", 20)))
    current = int(task_initial.get("current_temperature", target))
    brightness = int(task_initial.get("light_brightness", 100))
    projector = str(task_initial.get("projector", "off"))
    booking_confirmed = bool(task_initial.get("booked", False))
    return {
        "booking_service_available": True,
        "projector_service_available": True,
        "thermostat_service_available": True,
        "lighting_service_available": True,
        "booking_status": "confirmed" if booking_confirmed else "pending",
        "booking_confirmed": booking_confirmed,
        "thermostat_A": {
            "targetTemperature": target,
            "currentTemperature": current,
        },
        "lights": {"brightness": brightness},
        "projector_A": {"power": projector},
        "readiness": {"ready": False},
    }


def _compute_readiness(device_states: dict[str, Any]) -> bool:
    projector_on = (device_states.get("projector_A") or {}).get("power") == "on"
    target = (device_states.get("thermostat_A") or {}).get("targetTemperature")
    brightness = (device_states.get("lights") or {}).get("brightness")
    booking_confirmed = (
        bool(device_states.get("booking_confirmed")) or device_states.get("booking_status") == "confirmed"
    )
    return bool(booking_confirmed and target == 22 and projector_on and brightness is not None and brightness <= 40)


def _final_state_from_map(cognitive_map: CognitiveMap) -> dict[str, Any]:
    states = cognitive_map.device_states
    return {
        "booked": bool(states.get("booking_confirmed") or states.get("booking_status") == "confirmed"),
        "projector": (states.get("projector_A") or {}).get("power"),
        "target_temperature": (states.get("thermostat_A") or {}).get("targetTemperature"),
        "light_brightness": (states.get("lights") or {}).get("brightness"),
        "readiness": (states.get("readiness") or {}).get("ready"),
    }


def _failure_plan_for_task(task_id: str, allowed_failure_profile: str | None) -> tuple[dict[tuple[str, str], str], int]:
    if not allowed_failure_profile:
        return {}, 0

    expected_tier = 0
    for profile in load_failure_profiles():
        if profile.failure_id == allowed_failure_profile:
            expected_tier = int(profile.expected_recovery_tier)
            break

    if allowed_failure_profile == "dom_selector_mutation":
        return {("confirm_booking", "dom"): "selector_not_found"}, expected_tier
    if allowed_failure_profile in {"sensory_contradiction", "wot_postcondition_mismatch"}:
        return {("set_temperature", "wot"): "postcondition_mismatch"}, expected_tier
    return {}, expected_tier


async def _run_skill_with_recovery(
    manager: ContinuousInteractionManager,
    skill_call: SkillCall,
    make_observation: callable,
) -> FixtureStepResult:
    attempts = 0
    highest_tier = 0
    current_call = skill_call

    while attempts < 4:
        attempts += 1
        result: RuntimeStepResult = await manager.run_skill(current_call, make_observation())
        if result.state == RuntimeState.COMPLETED:
            return FixtureStepResult(
                skill_id=skill_call.skill_id,
                success=True,
                attempts=attempts,
                selected_backend=result.selected_backend,
                recovery_tier=highest_tier,
                reason=result.reason,
            )

        tier = int(result.recovery_tier or 4)
        highest_tier = max(highest_tier, tier)
        if tier == 1:
            continue
        if tier == 2:
            reroute = result.selected_backend or "visual"
            current_call = SkillCall(
                skill_id=skill_call.skill_id,
                params=dict(skill_call.params),
                preferred_backends=[reroute],
            )
            continue

        return FixtureStepResult(
            skill_id=skill_call.skill_id,
            success=False,
            attempts=attempts,
            selected_backend=result.selected_backend,
            recovery_tier=highest_tier,
            reason=result.reason,
        )

    return FixtureStepResult(
        skill_id=skill_call.skill_id,
        success=False,
        attempts=attempts,
        selected_backend=current_call.preferred_backends[0] if current_call.preferred_backends else "",
        recovery_tier=highest_tier or 4,
        reason="recovery loop exhausted",
    )


async def _evaluate_task(task_id: str) -> FixtureTaskEvaluation:
    fixture = get_task_fixture(task_id)
    expected_calls = expected_skill_calls(task_id)
    expected_sequence = [call.skill_id for call in expected_calls]

    fail_once, expected_recovery_tier = _failure_plan_for_task(task_id, fixture.allowed_failure_profile)

    cognitive_map = CognitiveMap(task_id=task_id)
    manager = ContinuousInteractionManager(
        load_skill_library(),
        {
            "dom": _DeterministicExecutor("dom", fail_once),
            "wot": _DeterministicExecutor("wot", fail_once),
            "visual": _DeterministicExecutor("visual", fail_once),
        },
        cognitive_map,
    )

    def make_observation() -> Observation:
        return Observation(
            device_states=dict(cognitive_map.device_states),
            accessibility_tree={
                "page_state": {"booking_status": cognitive_map.device_states.get("booking_status", "pending")}
            },
        )

    cognitive_map.update_from_observation(Observation(device_states=_canonical_device_states(fixture.initial_state)))

    step_results: list[FixtureStepResult] = []
    for call in expected_calls:
        step_result = await _run_skill_with_recovery(manager, call, make_observation)
        step_results.append(step_result)
        if not step_result.success:
            break

    executed_sequence = [step.skill_id for step in step_results]
    final_state = _final_state_from_map(cognitive_map)
    achieved_recovery_tier = max((step.recovery_tier for step in step_results), default=0)
    task_success = (
        bool(step_results)
        and all(step.success for step in step_results)
        and final_state == fixture.expected_final_state
    )

    return FixtureTaskEvaluation(
        task_id=task_id,
        task_success=task_success,
        skill_sequence_match=executed_sequence == expected_sequence,
        recovery_tier_match=achieved_recovery_tier == expected_recovery_tier,
        expected_recovery_tier=expected_recovery_tier,
        achieved_recovery_tier=achieved_recovery_tier,
        expected_skill_sequence=expected_sequence,
        executed_skill_sequence=executed_sequence,
        final_state=final_state,
        expected_final_state=dict(fixture.expected_final_state),
        steps=step_results,
    )


def evaluate_all_task_fixtures() -> dict[str, Any]:
    evaluations = [asyncio.run(_evaluate_task(fixture.task_id)) for fixture in load_task_fixtures()]

    dataset = EvaluationDataset(
        tasks=[
            TaskOutcome(
                task_id=e.task_id,
                final_success=e.task_success,
                recovery_triggered=e.achieved_recovery_tier > 0,
            )
            for e in evaluations
        ],
        recovery_cases=[
            RecoveryCase(
                task_id=e.task_id,
                failure_type=(get_task_fixture(e.task_id).allowed_failure_profile or "none"),
                expected_tier=e.expected_recovery_tier,
                triggered_tier=e.achieved_recovery_tier,
                recovery_success=e.recovery_tier_match,
                final_success=e.task_success,
            )
            for e in evaluations
        ],
    )

    metrics = aggregate_metrics(dataset).values
    summary = {
        "tasks": [
            {
                **asdict(e),
                "steps": [asdict(step) for step in e.steps],
            }
            for e in evaluations
        ],
        "metrics": {
            **metrics,
            "task_success_rate": sum(1 for e in evaluations if e.task_success) / len(evaluations),
            "skill_sequence_match_rate": sum(1 for e in evaluations if e.skill_sequence_match) / len(evaluations),
            "recovery_tier_match_rate": sum(1 for e in evaluations if e.recovery_tier_match) / len(evaluations),
        },
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate all Member A fixtures offline.")
    parser.add_argument("--output", default="artifacts/fixture_eval_report.json")
    args = parser.parse_args()

    report = evaluate_all_task_fixtures()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"report": str(output), "metrics": report["metrics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
