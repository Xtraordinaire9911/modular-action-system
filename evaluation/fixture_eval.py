"""Level-1 fixture regression used by the robustness harness."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.skill_library import expected_skill_calls, load_task_fixtures


def evaluate_all_task_fixtures() -> dict[str, Any]:
    """Validate built-in task fixtures without requiring browser services."""

    tasks = []
    for fixture in load_task_fixtures():
        calls = expected_skill_calls(fixture.task_id)
        executed = [call.skill_id for call in calls]
        sequence_match = executed == fixture.expected_skill_sequence
        tasks.append(
            {
                "task_id": fixture.task_id,
                "task_success": sequence_match,
                "skill_sequence_match": sequence_match,
                "expected_skill_sequence": list(fixture.expected_skill_sequence),
                "executed_skill_sequence": executed,
                "fixture": asdict(fixture),
            }
        )

    success_count = sum(1 for task in tasks if task["task_success"])
    total = len(tasks)
    task_success_rate = success_count / total if total else 0.0
    return {
        "level": 1,
        "tasks": tasks,
        "metrics": {
            "task_success_rate": task_success_rate,
            "TSR": task_success_rate,
        },
    }
