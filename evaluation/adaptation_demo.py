"""White-box adaptation demo for runtime recovery and trace-driven proposals."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from evaluation.metrics_aggregator import AdaptationCase, EvaluationDataset, aggregate_metrics
from src.adaptation.artifact_writer import write_adaptation_artifacts
from src.adaptation.llm_judge import LLMJudge
from src.adaptation.pattern_miner import FailurePatternMiner
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger
from src.contracts.types import Affordance, Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.state_machine import RuntimeState
from src.verification.active_perception import ActivePerceptionResolver


class _DemoExecutor:
    def __init__(
        self,
        backend: str,
        *,
        result: ExecutionResult | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.backend = backend
        self.result = result
        self.exception = exception
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        if self.exception is not None:
            raise self.exception
        return self.result or ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=True,
            latency_ms=10.0,
            confidence=1.0,
            raw_observation_delta={"thermostat_A": {"targetTemperature": skill_call.params.get("target", 22)}},
        )


class _BookingGoalExecutor:
    def __init__(self) -> None:
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        delta: dict[str, dict[str, object]] = {}
        if skill_call.params.get("primitive_action") == "click":
            delta = {"booking": {"confirmed": True}}
        if skill_call.params.get("primitive_action") == "type":
            affordance_id = str(skill_call.params.get("affordance_id", ""))
            value = skill_call.params.get("value")
            if "room" in affordance_id:
                delta = {"booking_form": {"room": value}}
            if "time" in affordance_id:
                delta = {"booking_form": {"time": value}}
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=True,
            latency_ms=12.0,
            confidence=1.0,
            raw_observation_delta=delta,
        )


class _DemoJudgeClient:
    def complete_json(self, prompt: str) -> dict[str, Any]:
        return {
            "boundary": "skill_spec_insufficient",
            "failure_type": "weak_postcondition",
            "confidence": 0.73,
            "evidence": ["demo LLM advisory: success signal is ambiguous"],
            "immediate_action": "use_recovery_cascade",
            "long_term_action": "strengthen_postcondition",
            "safe_to_auto_apply": False,
            "needs_human_review": True,
        }


class _DemoActivePerceptionProbe:
    async def observe(self, conflicts, cognitive_map, original_observation):
        return Observation(
            device_states={"thermostat_A": {"targetTemperature": 22}},
            accessibility_tree={"page_state": {"thermostat_A": {"targetTemperature": 22}}},
        )


def _skill_tuple(
    *,
    skill_id: str = "set_temperature",
    allowed_backends: list[str] | None = None,
    preferred_backends: list[str] | None = None,
    postconditions: list[Condition] | None = None,
) -> SkillTuple:
    return SkillTuple(
        skill_id=skill_id,
        description=skill_id,
        parameters_schema={},
        preconditions=[],
        postconditions=postconditions or [],
        allowed_backends=allowed_backends or ["wot", "dom", "visual"],
        preferred_backends=preferred_backends or ["wot", "dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
    )


def run_adaptation_demo(output_dir: str | Path = "artifacts/adaptation_demo") -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    scenarios = [
        asyncio.run(_sensory_conflict_scenario()),
        asyncio.run(_active_perception_resolved_scenario()),
        asyncio.run(_bounded_goal_no_durable_skill_scenario()),
        asyncio.run(_timeout_reroute_scenario()),
        asyncio.run(_llm_advisory_scenario()),
    ]

    ledger = _build_repeated_failure_ledger()
    proposals = FailurePatternMiner(min_support=3, min_distinct_incidents=2).mine(ledger)
    artifact_paths = write_adaptation_artifacts(ledger, proposals, target)
    metrics = _build_demo_metrics(scenarios, proposals)

    runtime_path = target / "runtime_failure_demo.json"
    metrics_path = target / "adaptation_metrics.json"
    runtime_path.write_text(
        json.dumps({"scenarios": scenarios}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "runtime_demo": runtime_path,
        "metrics": metrics_path,
        **artifact_paths,
    }


async def _sensory_conflict_scenario() -> dict[str, Any]:
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill_tuple()},
        {"wot": _DemoExecutor("wot")},
        CognitiveMap(task_id="demo_sensory_conflict"),
    )
    observation = Observation(
        device_states={"thermostat_A": {"targetTemperature": 24}},
        accessibility_tree={"page_state": {"thermostat_A": {"targetTemperature": 20}}},
    )
    result = await manager.run_skill(SkillCall("set_temperature", {"target": 22}), observation)
    return _result_payload("sensory_conflict_blocks_system1", result)


async def _active_perception_resolved_scenario() -> dict[str, Any]:
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill_tuple()},
        {"wot": _DemoExecutor("wot")},
        CognitiveMap(task_id="demo_active_perception_resolved"),
        active_perception_resolver=ActivePerceptionResolver(_DemoActivePerceptionProbe()),
    )
    observation = Observation(
        device_states={"thermostat_A": {"targetTemperature": 24}},
        accessibility_tree={"page_state": {"thermostat_A": {"targetTemperature": 20}}},
    )
    result = await manager.run_skill(SkillCall("set_temperature", {"target": 22}), observation)
    return _result_payload("sensory_conflict_resolved_by_active_perception", result)


async def _timeout_reroute_scenario() -> dict[str, Any]:
    manager = ContinuousInteractionManager(
        {
            "set_temperature": _skill_tuple(
                allowed_backends=["wot", "dom"],
                preferred_backends=["wot", "dom"],
            )
        },
        {
            "wot": _DemoExecutor("wot", exception=TimeoutError("demo timeout")),
            "dom": _DemoExecutor("dom"),
        },
        CognitiveMap(task_id="demo_timeout_reroute"),
    )
    result = await manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation())
    return _result_payload("executor_timeout_reroutes_with_trace", result)


async def _bounded_goal_no_durable_skill_scenario() -> dict[str, Any]:
    cognitive_map = CognitiveMap(task_id="demo_bounded_goal")
    cognitive_map.update_affordances(
        [
            _dom_affordance("dom_room_input", "input", "Room", "type", "booking_form", parameter="room"),
            _dom_affordance("dom_time_input", "input", "Time", "type", "booking_form", parameter="time"),
            _dom_affordance(
                "dom_confirm_booking",
                "button",
                "Confirm booking",
                "click",
                "booking_button",
                completion_for="reserve_room_goal",
                achieves="booking.confirmed == true",
            ),
        ]
    )
    manager = ContinuousInteractionManager(
        {},
        {"dom": _BookingGoalExecutor()},
        cognitive_map,
    )
    result = await manager.run_goal(
        goal_id="reserve_room_goal",
        goal_state="booking.confirmed == true",
        parameters={"room": "A", "time": "14:00"},
        observation=Observation(),
    )
    return _result_payload("bounded_goal_executes_without_durable_skill", result)


async def _llm_advisory_scenario() -> dict[str, Any]:
    manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "visual"],
                preferred_backends=["dom", "visual"],
            )
        },
        {
            "dom": _DemoExecutor(
                "dom",
                result=ExecutionResult(
                    skill_id="confirm_booking",
                    backend_used="dom",
                    success=False,
                    latency_ms=10.0,
                    confidence=0.2,
                    failure_reason="ambiguous_false_success",
                ),
            ),
            "visual": _DemoExecutor("visual"),
        },
        CognitiveMap(task_id="demo_llm_advisory"),
        llm_judge=LLMJudge(client=_DemoJudgeClient()),
        use_llm_judge=True,
    )
    result = await manager.run_skill(SkillCall("confirm_booking", {"room": "A"}), Observation())
    return _result_payload("optional_llm_advisory_judgment", result)


def _result_payload(name: str, result: Any) -> dict[str, Any]:
    return {
        "name": name,
        "state": result.state.value if isinstance(result.state, RuntimeState) else str(result.state),
        "reason": result.reason,
        "selected_backend": result.selected_backend,
        "recovery_tier": result.recovery_tier,
        "failure_boundary": result.failure_boundary,
        "failure_type": result.failure_type,
        "conflict_ids": result.conflict_ids,
        "recovery_trace": result.recovery_trace,
        "llm_failure_boundary": result.llm_failure_boundary,
        "llm_failure_type": result.llm_failure_type,
        "llm_judge_evidence": result.llm_judge_evidence,
        "active_perception_trace": result.active_perception_trace,
        "fusion_decision": result.fusion_decision,
        "primitive_plan": result.primitive_plan,
        "plan_validation_errors": result.plan_validation_errors,
        "episode_id": result.episode_id,
        "attempts": result.attempts,
        "transition_ids": result.transition_ids,
        "recovery_attempted": result.recovery_attempted,
        "recovery_succeeded": result.recovery_succeeded,
        "final_outcome_verified": result.final_outcome_verified,
        "execution_result": asdict(result.execution_result) if result.execution_result else None,
    }


def _dom_affordance(
    affordance_id: str,
    affordance_type: Literal["button", "input", "property", "action", "event", "sensor"],
    label: str,
    action: str,
    entity_id: str,
    *,
    parameter: str = "",
    completion_for: str = "",
    achieves: str = "",
) -> Affordance:
    locator = {"entity_id": entity_id}
    if parameter:
        locator["parameter"] = parameter
    if completion_for:
        locator["completion_for"] = completion_for
    if achieves:
        locator["achieves"] = achieves
    return Affordance(
        id=affordance_id,
        source="DOM",
        type=affordance_type,
        label=label,
        action=action,
        locator=locator,
        confidence=0.95,
    )


def _build_repeated_failure_ledger() -> TraceLedger:
    ledger = TraceLedger()
    for index in range(4):
        ledger.record(
            EpisodeFailureEvent(
                episode_id=f"demo_ep_{index}",
                task_id="prepare_room_A",
                skill_id="set_temperature",
                backend="wot",
                failure_type="timeout",
                boundary="immediate_runtime_error",
                context_key="smart_room:thermostat",
                incident_id=f"incident_{index}",
                recovery_action="reroute",
                recovery_success=True,
            )
        )
    return ledger


def _build_demo_metrics(scenarios: list[dict[str, Any]], proposals: list[Any]) -> dict[str, Any]:
    dataset = EvaluationDataset(
        adaptation_cases=[
            AdaptationCase(
                task_id=scenario["name"],
                failure_classified=bool(scenario["failure_boundary"] or scenario["llm_failure_boundary"]),
                full_cascade_trace=bool(scenario["recovery_trace"]),
                recoverable=scenario["recovery_tier"] in {1, 2, 3, 4},
                recovered=bool(scenario["recovery_succeeded"] and scenario["final_outcome_verified"]),
                policy_proposal_created=bool(proposals),
                time_to_recovery_ms=10.0,
                false_success_case=scenario["name"] == "optional_llm_advisory_judgment",
                false_success_detected=bool(scenario["llm_failure_type"]),
                normal_outcome_score=1.0,
                failure_outcome_score=0.8 if scenario["state"] in {"recovering", "escalated"} else 0.0,
                before_heldout_success_rate=0.4,
                after_heldout_success_rate=0.6,
                before_normal_success_rate=1.0,
                after_normal_success_rate=1.0,
                safety_regression=False,
                path_attributed=bool(
                    scenario["failure_type"] or scenario["llm_failure_type"] or scenario["conflict_ids"]
                ),
            )
            for scenario in scenarios
        ]
    )
    report = aggregate_metrics(dataset, data_source="synthetic")
    return {"data_source": report.metadata["data_source"], **report.values}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the white-box adaptation demo.")
    parser.add_argument("--output-dir", default="artifacts/adaptation_demo")
    args = parser.parse_args()
    paths = run_adaptation_demo(args.output_dir)
    print(json.dumps({key: str(path) for key, path in paths.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
