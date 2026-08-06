"""Run task-specific benchmark solvers through the shared episode runner.

This is an explicit scripted-solver envelope. It unifies episode ids, ledgers,
postcondition checks, and metrics without claiming the solver is an agentic
GoalSpec planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall, SkillTuple
from src.runtime.episode_runner import RuntimeEpisodeOutcome, RuntimeEpisodeRunner, RuntimeEpisodeSpec
from src.runtime.episode_runner import StaticRuntimeEnvironmentAdapter

SCRIPTED_SOLVER_SKILL = "scripted_solver"


@dataclass
class ScriptedRuntimeOutcome:
    result: Any
    scripted_outcome: dict[str, Any]
    runtime_entrypoint: str
    transition_ledger: Any
    metrics: Any


class ScriptedTaskExecutor:
    def __init__(self, run: Callable[[], dict[str, Any]]) -> None:
        self._run = run
        self.outcome: dict[str, Any] = {}
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        self.outcome = dict(self._run())
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="system",
            success=True,
            latency_ms=float(self.outcome.get("latency_ms", 0.0) or 0.0),
            confidence=1.0,
            raw_observation_delta={
                "scripted": {
                    "success": bool(self.outcome.get("success")),
                    "solver_type": "scripted",
                }
            },
            observation_source="system",
            metadata={"scripted_outcome": self.outcome},
        )


async def run_scripted_task_episode(
    *,
    task_id: str,
    run: Callable[[], dict[str, Any]],
    data_source: str = "scripted_benchmark",
) -> ScriptedRuntimeOutcome:
    executor = ScriptedTaskExecutor(run)
    outcome: RuntimeEpisodeOutcome = await RuntimeEpisodeRunner(
        skill_library={
            SCRIPTED_SOLVER_SKILL: SkillTuple(
                skill_id=SCRIPTED_SOLVER_SKILL,
                description="Run a task-specific scripted benchmark solver inside the runtime episode envelope.",
                parameters_schema={},
                preconditions=[],
                postconditions=[Condition("scripted.success == true")],
                allowed_backends=["system"],
                preferred_backends=["system"],
                rollback=None,
                failure_modes={},
                timeout_ms=60_000,
                safety_level="low",
                irreversible=False,
                idempotent=False,
            )
        }
    ).run_skill_episode(
        StaticRuntimeEnvironmentAdapter({"system": executor}),
        SkillCall(SCRIPTED_SOLVER_SKILL, {"solver_type": "scripted"}),
        RuntimeEpisodeSpec(task_id=task_id, data_source=data_source),
    )
    return ScriptedRuntimeOutcome(
        result=outcome.result,
        scripted_outcome=executor.outcome,
        runtime_entrypoint="RuntimeEpisodeRunner.run_skill_episode",
        transition_ledger=outcome.transition_ledger,
        metrics=outcome.metrics,
    )
