"""Deterministic vertical-slice demo traces for runtime control evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.metrics_aggregator import EvaluationDataset, PrimitiveAction, RecoveryCase, TaskOutcome
from evaluation.recovery_eval import write_recovery_metrics
from src.runtime.cognitive_map import CognitiveMap, Entity, RuntimeAffordance, StateAssertion
from src.skill_library import expected_skill_calls, get_task_fixture, load_failure_profiles
from src.verification.conflict_detector import EpistemicArbiter

DEMO_TIMESTAMP_MS = 1_781_000_000_000
DEMO_TASK_ID = "prepare_room_A_1400"


def _demo_target_temperature() -> int:
    """Read the target temperature for the demo task from the fixtures."""
    for call in expected_skill_calls(DEMO_TASK_ID):
        if call.skill_id == "set_temperature":
            return int(call.params["target"])
    raise ValueError(f"demo task fixture {DEMO_TASK_ID!r} has no set_temperature step")


def _event(task_id: str, skill_id: str, event_type: str, backend: str | None, details: dict[str, Any]) -> dict:
    return {
        "timestamp_ms": DEMO_TIMESTAMP_MS,
        "task_id": task_id,
        "skill_id": skill_id,
        "event_type": event_type,
        "backend": backend,
        "details": details,
    }


def build_demo_cognitive_map(task_id: str = "prepare_room_A_1400") -> CognitiveMap:
    cmap = CognitiveMap(task_id=task_id)
    cmap.add_entity(Entity(id="thermostat_A", type="thermostat", name="Room A thermostat"))
    cmap.add_affordance(
        RuntimeAffordance(
            id="wot_thermostat_A_setTargetTemperature",
            source="wot",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="invoke",
            confidence=1.0,
            grounding={"href": "/thermostat/actions/setTargetTemperature", "method": "POST"},
            input_schema={"target": "int"},
            skill_names=["set_temperature"],
        )
    )
    return cmap


def run_normal_demo_trace() -> list[dict]:
    fixture = get_task_fixture(DEMO_TASK_ID)
    target = _demo_target_temperature()
    task_id = f"{fixture.task_id}_normal"
    skill_id = "set_temperature"
    cmap = build_demo_cognitive_map(task_id)
    events = [
        _event(task_id, skill_id, "task_started", None, {"goal": fixture.user_goal}),
        _event(task_id, skill_id, "skill_started", None, {"params": {"room": "A", "target": target}}),
        _event(task_id, skill_id, "cognitive_map_updated", None, {"entities": list(cmap.entities)}),
        _event(task_id, skill_id, "precondition_checked", None, {"passed": True}),
        _event(
            task_id,
            skill_id,
            "backend_selected",
            "wot",
            {"affordance_id": "wot_thermostat_A_setTargetTemperature", "reason": "WoT affordance available"},
        ),
        _event(task_id, skill_id, "primitive_executed", "wot", {"action": "invoke", "success": True}),
    ]
    cmap.add_state_assertion(
        StateAssertion(
            entity_id="thermostat_A",
            attribute="targetTemperature",
            value=target,
            source="wot",
        )
    )
    events.extend(
        [
            _event(
                task_id,
                skill_id,
                "postcondition_checked",
                "wot",
                {"passed": True, "expected": target, "actual": target},
            ),
            _event(task_id, skill_id, "skill_completed", "wot", {"final_status": "success"}),
            _event(task_id, skill_id, "task_completed", None, {"ready": True}),
        ]
    )
    return events


def run_recovery_demo_trace() -> list[dict]:
    fixture = get_task_fixture(DEMO_TASK_ID)
    target = _demo_target_temperature()
    profile = next(p for p in load_failure_profiles() if p.failure_id == "sensory_contradiction")
    dom_value = int(profile.extra["dom_value"])
    wot_value = int(profile.extra["wot_value"])
    task_id = f"{fixture.task_id}_recovery"
    skill_id = "set_temperature"
    cmap = build_demo_cognitive_map(task_id)
    events = [
        _event(task_id, skill_id, "task_started", None, {"failure_injection": "wot_state_mismatch"}),
        _event(task_id, skill_id, "skill_started", None, {"params": {"room": "A", "target": target}}),
        _event(task_id, skill_id, "precondition_checked", None, {"passed": True}),
        _event(task_id, skill_id, "backend_selected", "wot", {"reason": "preferred physical backend"}),
        _event(task_id, skill_id, "primitive_executed", "wot", {"action": "invoke", "success": True}),
    ]
    cmap.add_state_assertion(StateAssertion("thermostat_A", "targetTemperature", dom_value, "dom"))
    cmap.add_state_assertion(StateAssertion("thermostat_A", "targetTemperature", wot_value, "wot"))
    conflicts = EpistemicArbiter({"targetTemperature": 2.0}).check(cmap)
    events.append(
        _event(
            task_id,
            skill_id,
            "conflict_detected",
            None,
            {"conflicts": [conflict.__dict__ for conflict in conflicts], "halt_system1": True},
        )
    )
    events.extend(
        [
            _event(
                task_id,
                skill_id,
                "postcondition_checked",
                "wot",
                {"passed": False, "expected": target, "actual": wot_value},
            ),
            _event(task_id, skill_id, "recovery_triggered", None, {"policy": "active_perception_then_retry"}),
        ]
    )
    cmap.add_state_assertion(StateAssertion("thermostat_A", "targetTemperature", target, "wot"))
    events.extend(
        [
            _event(task_id, skill_id, "primitive_executed", "wot", {"action": "read_property", "success": True}),
            _event(
                task_id,
                skill_id,
                "postcondition_checked",
                "wot",
                {"passed": True, "expected": target, "actual": target},
            ),
            _event(task_id, skill_id, "skill_completed", "wot", {"final_status": "recovered"}),
            _event(task_id, skill_id, "task_completed", None, {"ready": True}),
        ]
    )
    return events


def write_demo_artifacts(output_dir: str | Path = "artifacts") -> dict[str, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    normal = run_normal_demo_trace()
    recovery = run_recovery_demo_trace()
    normal_path = target / "demo_trace_normal.json"
    recovery_path = target / "demo_trace_recovery.json"
    metrics_path = target / "recovery_metrics.json"
    normal_path.write_text(json.dumps(normal, indent=2, sort_keys=True), encoding="utf-8")
    recovery_path.write_text(json.dumps(recovery, indent=2, sort_keys=True), encoding="utf-8")
    dataset = EvaluationDataset(
        tasks=[
            TaskOutcome("normal", final_success=True, latency_ms=100, recovery_triggered=False),
            TaskOutcome("recovery", final_success=True, latency_ms=180, recovery_triggered=True),
        ],
        recovery_cases=[
            RecoveryCase(
                "recovery",
                "wot_state_mismatch",
                expected_tier=1,
                triggered_tier=1,
                recovery_success=True,
                final_success=True,
            )
        ],
        primitive_actions=[
            PrimitiveAction("normal", "invoke", 40),
            PrimitiveAction("recovery", "invoke", 60),
            PrimitiveAction("recovery", "read_property", 30),
        ],
    )
    write_recovery_metrics(dataset, metrics_path)
    return {"normal": normal_path, "recovery": recovery_path, "metrics": metrics_path}


def main() -> None:
    paths = write_demo_artifacts()
    print(json.dumps({key: str(path) for key, path in paths.items()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
