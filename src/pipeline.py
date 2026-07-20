"""Top-level runtime entry point.

The Week-6 demo has concrete DOM/WoT/Visual perception and executor modules;
this file keeps a tiny deterministic orchestration smoke test for CI while also
offering a white-box runtime demo entry point that does not require browser or
Docker services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from src.contracts.types import Condition, ExecutionResult, Observation, RollbackSpec, SkillCall, SkillTuple
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager


class NoOpExecutor:
    """Executor used only for CI smoke runs that avoid browser/Docker services."""

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="noop",
            success=True,
            latency_ms=0.0,
            confidence=1.0,
            raw_observation_delta={"pipeline": {"smoke_completed": True}},
        )


def build_smoke_skill_library() -> dict[str, SkillTuple]:
    return {
        "pipeline_smoke": SkillTuple(
            skill_id="pipeline_smoke",
            description="Validate that runtime orchestration can execute one safe smoke skill.",
            parameters_schema={},
            preconditions=[],
            postconditions=[Condition("device_states.pipeline.smoke_completed == true")],
            allowed_backends=["noop"],
            preferred_backends=["noop"],
            rollback=RollbackSpec("pipeline_smoke_rollback", {}),
            failure_modes={},
            timeout_ms=1000,
            safety_level="low",
            irreversible=False,
        )
    }


async def run_smoke_pipeline(task_id: str = "pipeline_smoke_task") -> dict:
    cognitive_map = CognitiveMap(task_id=task_id)
    manager = ContinuousInteractionManager(
        skill_library=build_smoke_skill_library(),
        executors={"noop": NoOpExecutor()},
        cognitive_map=cognitive_map,
    )
    result = await manager.run_skill(
        SkillCall(skill_id="pipeline_smoke", params={}),
        Observation(),
    )
    return {
        "task_id": task_id,
        "state": result.state.value,
        "selected_backend": result.selected_backend,
        "reason": result.reason,
        "recovery_tier": result.recovery_tier,
        "execution_result": asdict(result.execution_result) if result.execution_result else None,
        "cognitive_map": cognitive_map.snapshot(),
    }


def run_runtime_demo_pipeline(output_dir: str | Path = "artifacts/adaptation_demo") -> dict[str, str]:
    """Run the integrated runtime-control demo and return written artifacts."""

    from evaluation.adaptation_demo import run_adaptation_demo

    return {key: str(path) for key, path in run_adaptation_demo(output_dir).items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the modular action system pipeline.")
    parser.add_argument("--smoke", action="store_true", help="Run the current smoke orchestration path.")
    parser.add_argument("--demo", action="store_true", help="Run the white-box runtime-control demo path.")
    parser.add_argument("--output-dir", default="artifacts/adaptation_demo")
    parser.add_argument("--task-id", default="pipeline_smoke_task")
    args = parser.parse_args()

    if args.demo:
        summary = run_runtime_demo_pipeline(args.output_dir)
    else:
        # --smoke is accepted for explicit CI usage; the default path avoids
        # live services so it stays deterministic.
        _ = args.smoke
        summary = asyncio.run(run_smoke_pipeline(task_id=args.task_id))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
