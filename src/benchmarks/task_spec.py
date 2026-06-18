"""Task and result contracts for external CUA/web benchmark runs.

The smart-room demo proves the action system on one bespoke environment. To
back the project's environment-generalization claim we also run the *same*
perceive -> act -> verify stack against third-party benchmarks (WebArena,
VisualWebArena, MiniWoB++, ...). These dataclasses are the benchmark-agnostic
contract between a task definition and a single run's outcome, mirroring the
backend-level ``ExecutionResult`` style used elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # avoid an import cycle; only needed for the success-check signature
    from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter


@dataclass
class BenchmarkTask:
    """One external-benchmark task expressed in our own vocabulary."""

    env: str
    task_id: str
    start_url: str
    goal: str
    # Lightweight, benchmark-agnostic success proxy: every fragment must appear
    # in the page's visible text. Real benchmarks plug their own evaluator via
    # ``success_check`` instead (which takes the live adapter).
    success_text: list[str] = field(default_factory=list)
    success_check: Callable[["WebBenchmarkAdapter"], bool] | None = None
    max_steps: int = 20

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "task_id": self.task_id,
            "start_url": self.start_url,
            "goal": self.goal,
            "success_text": self.success_text,
            "has_custom_check": self.success_check is not None,
            "max_steps": self.max_steps,
        }


@dataclass
class BenchmarkRunResult:
    """Outcome of one benchmark task run through the action system."""

    env: str
    task_id: str
    success: bool
    steps: int
    latency_ms: float
    backend_counts: dict[str, int] = field(default_factory=dict)
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "task_id": self.task_id,
            "success": self.success,
            "steps": self.steps,
            "latency_ms": self.latency_ms,
            "backend_counts": self.backend_counts,
            "failure_reason": self.failure_reason,
        }
