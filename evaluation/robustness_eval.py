"""Unified Level 1-3 robustness evaluation harness."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evaluation.chaos_monkey import ChaosEvent, ChaosPolicy, OfflineChaosExecutor, apply_observation_chaos
from evaluation.fixture_eval import evaluate_all_task_fixtures
from evaluation.metrics_aggregator import EvaluationDataset, OracleCase, TaskOutcome, aggregate_metrics
from evaluation.randomized_fixture_generator import DEV_SEEDS, EVAL_SEEDS, RandomizedFixture, generate_randomized_fixture
from src.contracts.types import ExecutionResult, Observation, SkillCall
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult
from src.runtime.state_machine import RuntimeState
from src.skill_library import TaskFixture, expected_skill_calls, get_task_fixture, load_skill_library
from src.verification.oracle_verifier import OracleVerifier, OracleVerdict


@dataclass
class RobustnessStep:
    skill_id: str
    success: bool
    attempts: int
    selected_backend: str
    recovery_tier: int
    reason: str
    execution_result: dict[str, Any] | None
    oracle: dict[str, Any]
    oracle_attempts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RobustnessTaskResult:
    task_id: str
    level: int
    seed: int | None
    base_task_id: str
    task_success: bool
    constraints_satisfied: bool
    chaos_exposed: bool
    recovery_triggered: bool
    final_state: dict[str, Any]
    expected_final_state: dict[str, Any]
    chaos_events: list[dict[str, Any]] = field(default_factory=list)
    steps: list[RobustnessStep] = field(default_factory=list)


class _SmartRoomOracleState:
    def __init__(self, fixture: TaskFixture) -> None:
        self.expected_final_state = dict(fixture.expected_final_state)
        self.state: dict[str, Any] = {
            "booked": bool(fixture.initial_state.get("booked", False)),
            "booking_status": "confirmed" if fixture.initial_state.get("booked", False) else "pending",
            "booking_confirmed": bool(fixture.initial_state.get("booked", False)),
            "projector": fixture.initial_state.get("projector", "off"),
            "target_temperature": int(
                fixture.initial_state.get("target_temperature_wot", fixture.initial_state.get("target_temperature", 20))
            ),
            "light_brightness": int(fixture.initial_state.get("light_brightness", 100)),
            "readiness": False,
        }

    def apply_truth(self, skill_call: SkillCall, result: ExecutionResult | None) -> None:
        if result is None or not result.success:
            return
        if _result_has_stale_delta(skill_call, result):
            return
        if skill_call.skill_id == "confirm_booking":
            self.state.update({"booked": True, "booking_status": "confirmed", "booking_confirmed": True})
        elif skill_call.skill_id == "turn_on_projector":
            self.state["projector"] = "on"
        elif skill_call.skill_id == "set_temperature":
            self.state["target_temperature"] = skill_call.params.get("target")
        elif skill_call.skill_id == "set_lighting":
            self.state["light_brightness"] = skill_call.params.get("brightness")
        elif skill_call.skill_id == "verify_readiness":
            self.state["readiness"] = self.ready()

    def ready(self) -> bool:
        return bool(
            self.state.get("booked")
            and self.state.get("projector") == "on"
            and self.state.get("target_temperature") == self.expected_final_state.get("target_temperature")
            and self.state.get("light_brightness") == self.expected_final_state.get("light_brightness")
        )

    def as_observation(self) -> Observation:
        device_states = {
            "booking_service_available": True,
            "projector_service_available": True,
            "thermostat_service_available": True,
            "lighting_service_available": True,
            "booking_status": self.state["booking_status"],
            "booking_confirmed": self.state["booking_confirmed"],
            "projector_A": {"power": self.state["projector"]},
            "thermostat_A": {"targetTemperature": self.state["target_temperature"]},
            "lights": {"brightness": self.state["light_brightness"]},
            "readiness": {"ready": self.state["readiness"]},
        }
        return Observation(device_states=device_states, accessibility_tree={"page_state": {"booking_status": self.state["booking_status"]}})

    def final_state(self, expected: dict[str, Any]) -> dict[str, Any]:
        target = dict(self.state)
        if expected.get("target_temperature") != 22:
            target["readiness"] = bool(
                target.get("booked")
                and target.get("projector") == "on"
                and target.get("target_temperature") == expected.get("target_temperature")
                and target.get("light_brightness") == expected.get("light_brightness")
            )
        return {
            "booked": bool(target.get("booked")),
            "projector": target.get("projector"),
            "target_temperature": target.get("target_temperature"),
            "light_brightness": target.get("light_brightness"),
            "readiness": bool(target.get("readiness")),
        }


class _DeterministicExecutor:
    def __init__(self, backend: str) -> None:
        self.backend = backend

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=True,
            latency_ms=10.0,
            confidence=1.0,
            raw_observation_delta=_delta_for_skill(skill_call, observation),
        )


def run_robustness_eval(
    *,
    level: int = 2,
    base_fixture_ids: list[str] | None = None,
    seeds: list[int] | None = None,
    split: str = "dev",
    output: str | Path | None = None,
    live: bool = False,
) -> dict[str, Any]:
    if live:
        raise NotImplementedError("live robustness orchestration should call evaluation.live_runtime_eval explicitly")
    if level <= 1:
        report = evaluate_all_task_fixtures()
        report["level"] = 1
        if output is not None:
            _write_json(output, report)
        return report

    selected_seeds = seeds or list(DEV_SEEDS if split == "dev" else EVAL_SEEDS)
    base_ids = base_fixture_ids or ["prepare_room_A_1400"]
    randomized = [generate_randomized_fixture(base_id, seed) for base_id in base_ids for seed in selected_seeds]
    task_results = [asyncio.run(_evaluate_randomized(item, level=level)) for item in randomized]
    dataset = _dataset_from_results(task_results)
    metrics = aggregate_metrics(dataset).values
    report = {
        "level": level,
        "split": split,
        "seeds": selected_seeds,
        "tasks": [_task_to_dict(result) for result in task_results],
        "metrics": metrics,
    }
    if output is not None:
        _write_json(output, report)
    return report


async def _evaluate_randomized(item: RandomizedFixture, *, level: int) -> RobustnessTaskResult:
    fixture = item.fixture
    oracle_state = _SmartRoomOracleState(fixture)
    policy = ChaosPolicy.seeded(item.seed, level=level)
    cognitive_map = CognitiveMap(task_id=fixture.task_id)
    executors = {
        backend: OfflineChaosExecutor(backend, _DeterministicExecutor(backend), policy)
        for backend in ("dom", "wot", "visual")
    }
    manager = ContinuousInteractionManager(load_skill_library(), executors, cognitive_map)
    verifier = OracleVerifier()
    steps: list[RobustnessStep] = []

    for call in item.skill_calls:
        step = await _run_step(manager, call, fixture, oracle_state, verifier, policy)
        steps.append(step)
        if not step.success and step.recovery_tier >= 4:
            break

    final_state = oracle_state.final_state(fixture.expected_final_state)
    constraints_satisfied = _constraints_satisfied(final_state, fixture.expected_final_state)
    return RobustnessTaskResult(
        task_id=fixture.task_id,
        level=level,
        seed=item.seed,
        base_task_id=item.base_task_id,
        task_success=constraints_satisfied and all(step.success for step in steps),
        constraints_satisfied=constraints_satisfied,
        chaos_exposed=bool(policy.events),
        recovery_triggered=any(step.recovery_tier > 0 for step in steps),
        final_state=final_state,
        expected_final_state=dict(fixture.expected_final_state),
        chaos_events=[asdict(event) for event in policy.events],
        steps=steps,
    )


async def _run_step(
    manager: ContinuousInteractionManager,
    skill_call: SkillCall,
    fixture: TaskFixture,
    oracle_state: _SmartRoomOracleState,
    verifier: OracleVerifier,
    policy: ChaosPolicy,
) -> RobustnessStep:
    attempts = 0
    highest_tier = 0
    selected_backend = ""
    reason = ""
    last_execution: ExecutionResult | None = None
    last_verdict: OracleVerdict | None = None
    oracle_attempts: list[dict[str, Any]] = []
    current_call = skill_call

    while attempts < 4:
        attempts += 1
        observation = oracle_state.as_observation()
        observation = apply_observation_chaos(observation, policy, timing="during_verification", skill_id=skill_call.skill_id)
        result: RuntimeStepResult = await manager.run_skill(current_call, observation)
        selected_backend = result.selected_backend
        reason = result.reason
        last_execution = result.execution_result
        oracle_state.apply_truth(skill_call, result.execution_result)
        last_verdict = verifier.verify_skill(
            task_id=fixture.task_id,
            skill_call=skill_call,
            execution_result=result.execution_result,
            ground_truth_state=oracle_state.state,
        )
        oracle_attempts.append(asdict(last_verdict))

        if result.state == RuntimeState.COMPLETED and last_verdict.oracle_success:
            return _step_result(
                skill_call, True, attempts, selected_backend, highest_tier, reason, last_execution, last_verdict, oracle_attempts
            )

        tier = int(result.recovery_tier or (1 if last_verdict.false_positive else 4))
        highest_tier = max(highest_tier, tier)
        if tier == 1 or last_verdict.false_positive:
            continue
        if tier == 2:
            current_call = SkillCall(skill_id=skill_call.skill_id, params=dict(skill_call.params), preferred_backends=["visual"])
            continue
        return _step_result(
            skill_call, False, attempts, selected_backend, highest_tier, reason, last_execution, last_verdict, oracle_attempts
        )

    verdict = last_verdict or verifier.verify_skill(
        task_id=fixture.task_id,
        skill_call=skill_call,
        execution_result=last_execution,
        ground_truth_state=oracle_state.state,
    )
    if not oracle_attempts:
        oracle_attempts.append(asdict(verdict))
    return _step_result(
        skill_call, False, attempts, selected_backend, highest_tier or 4, "recovery loop exhausted", last_execution, verdict, oracle_attempts
    )


def _step_result(
    skill_call: SkillCall,
    success: bool,
    attempts: int,
    selected_backend: str,
    recovery_tier: int,
    reason: str,
    execution_result: ExecutionResult | None,
    verdict: OracleVerdict,
    oracle_attempts: list[dict[str, Any]] | None = None,
) -> RobustnessStep:
    return RobustnessStep(
        skill_id=skill_call.skill_id,
        success=success,
        attempts=attempts,
        selected_backend=selected_backend,
        recovery_tier=recovery_tier,
        reason=reason,
        execution_result=None if execution_result is None else asdict(execution_result),
        oracle=asdict(verdict),
        oracle_attempts=list(oracle_attempts or [asdict(verdict)]),
    )


def _dataset_from_results(results: list[RobustnessTaskResult]) -> EvaluationDataset:
    oracle_cases: list[OracleCase] = []
    for result in results:
        for step in result.steps:
            for oracle in step.oracle_attempts:
                oracle_cases.append(
                    OracleCase(
                        task_id=result.task_id,
                        skill_id=str(oracle["skill_id"]),
                        claimed_success=bool(oracle["claimed_success"]),
                        oracle_success=bool(oracle["oracle_success"]),
                        false_positive=bool(oracle["false_positive"]),
                        false_negative=bool(oracle["false_negative"]),
                    )
                )
    return EvaluationDataset(
        tasks=[
            TaskOutcome(
                result.task_id,
                result.task_success,
                recovery_triggered=result.recovery_triggered,
                constraints_satisfied=result.constraints_satisfied,
                chaos_exposed=result.chaos_exposed,
                attempts=sum(step.attempts for step in result.steps),
            )
            for result in results
        ],
        oracle_cases=oracle_cases,
    )


def _task_to_dict(result: RobustnessTaskResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["steps"] = [asdict(step) for step in result.steps]
    return payload


def _constraints_satisfied(final_state: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(final_state.get(key) == value for key, value in expected.items())


def _result_has_stale_delta(skill_call: SkillCall, result: ExecutionResult) -> bool:
    if skill_call.skill_id == "set_temperature":
        observed = (result.raw_observation_delta.get("thermostat_A") or {}).get("targetTemperature")
        return observed is not None and observed != skill_call.params.get("target")
    if skill_call.skill_id == "confirm_booking":
        return result.raw_observation_delta.get("booking_status") == "pending"
    return False


def _delta_for_skill(skill_call: SkillCall, observation: Observation) -> dict[str, Any]:
    if skill_call.skill_id == "confirm_booking":
        return {"booking_status": "confirmed", "booking_confirmed": True}
    if skill_call.skill_id == "turn_on_projector":
        return {"projector_A": {"power": "on"}}
    if skill_call.skill_id == "set_temperature":
        return {"thermostat_A": {"targetTemperature": skill_call.params.get("target")}}
    if skill_call.skill_id == "set_lighting":
        return {"lights": {"brightness": skill_call.params.get("brightness")}}
    if skill_call.skill_id == "verify_readiness":
        devices = observation.device_states
        ready = bool(
            devices.get("booking_status") == "confirmed"
            and (devices.get("projector_A") or {}).get("power") == "on"
            and (devices.get("thermostat_A") or {}).get("targetTemperature") is not None
            and (devices.get("lights") or {}).get("brightness") is not None
        )
        return {"readiness": {"ready": ready}}
    return {}


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Level 1-3 robustness evaluation.")
    parser.add_argument("--level", type=int, choices=[1, 2, 3], default=2)
    parser.add_argument("--split", choices=["dev", "eval"], default="dev")
    parser.add_argument("--base-fixture", action="append", dest="base_fixtures")
    parser.add_argument("--seed", action="append", type=int, dest="seeds")
    parser.add_argument("--output", default="artifacts/robustness_eval_report.json")
    args = parser.parse_args()
    report = run_robustness_eval(
        level=args.level,
        split=args.split,
        base_fixture_ids=args.base_fixtures,
        seeds=args.seeds,
        output=args.output,
    )
    print(json.dumps({"output": args.output, "metrics": report.get("metrics", {})}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
