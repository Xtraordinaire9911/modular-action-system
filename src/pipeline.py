"""Scaffold entry point for the modular action system.

This keeps Dockerfile's ``python -m src.pipeline`` command runnable before the
full runtime, perception, and effector implementations are integrated.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from src.contracts.types import ExecutionResult, SkillCall


def run_scaffold_smoke(task_id: str = "scaffold_smoke_task") -> dict:
    """Run a minimal smoke path using only scaffold-level contracts."""
    skill_call = SkillCall(skill_id="pipeline_smoke", params={})
    result = ExecutionResult(
        skill_id=skill_call.skill_id,
        backend_used="scaffold",
        success=True,
        latency_ms=0.0,
        confidence=1.0,
        raw_observation_delta={"pipeline": {"smoke_completed": True}},
    )
    return {
        "task_id": task_id,
        "state": "completed",
        "selected_backend": result.backend_used,
        "reason": "scaffold pipeline entry point is runnable",
        "execution_result": asdict(result),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the modular action system scaffold entry point.")
    parser.add_argument("--smoke", action="store_true", help="Run the scaffold smoke path.")
    parser.add_argument("--task-id", default="scaffold_smoke_task")
    args = parser.parse_args()

    _ = args.smoke
    print(json.dumps(run_scaffold_smoke(task_id=args.task_id), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
