"""Adapter that exposes an external web benchmark through our action surface.

Generalization claim, made concrete: instead of a second bespoke runtime, we
wrap any external benchmark page in the *same* perceive -> act -> verify
interface the smart-room runtime already uses. Perception is the DOM Transducer
(via ``BrowserSession.state``); action is the System-1 ``DomExecutor`` over the
isolated Playwright context (the CUA session-isolation boundary); verification
is the task's success proxy or its own evaluator.

The session/executor are injected, so this unit-tests with a fake page and
never needs a browser or network. The real benchmark is reached by passing a
live ``BrowserSession`` launched against the benchmark URL.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from src.benchmarks.task_spec import BenchmarkRunResult, BenchmarkTask
from src.contracts.types import Affordance, ExecutionResult
from src.effectors.dom_executor import DomExecutor
from src.perception.page_affordance_model import PageAffordanceModel


class SessionLike(Protocol):
    """Minimal surface we need from ``BrowserSession`` (or a test fake)."""

    def open(self, url: str) -> Any: ...
    def state(self, *, page_id: str = ..., captured_at_ms: int = ...) -> PageAffordanceModel: ...
    def click(self, selector: str) -> Any: ...
    def fill(self, selector: str, value: str) -> Any: ...
    def text_content(self, selector: str) -> str | None: ...


class WebBenchmarkAdapter:
    """Drive an external web benchmark with our perception + System-1 executor."""

    def __init__(self, session: SessionLike, *, executor: DomExecutor | None = None) -> None:
        self._session = session
        # The session itself satisfies DomExecutor's PageLike (click/fill/text_content).
        self._executor = executor or DomExecutor(session)

    # ── perceive / act / verify ───────────────────────────────────────────────
    def reset(self, task: BenchmarkTask) -> PageAffordanceModel:
        self._session.open(task.start_url)
        return self.observe(task)

    def observe(self, task: BenchmarkTask) -> PageAffordanceModel:
        return self._session.state(page_id=f"{task.env}:{task.task_id}")

    def act(self, affordance: Affordance, *, value: Any | None = None, skill_id: str = "") -> ExecutionResult:
        return self._executor.execute(affordance, value=value, skill_id=skill_id)

    def page_text(self) -> str:
        """Visible body text — a benchmark-agnostic signal for the success proxy."""
        try:
            return self._session.text_content("body") or ""
        except Exception:
            return ""

    def is_solved(self, task: BenchmarkTask) -> bool:
        if task.success_check is not None:
            return bool(task.success_check(self))
        if not task.success_text:
            return False
        text = self.page_text().lower()
        return all(fragment.strip().lower() in text for fragment in task.success_text)

    # ── end-to-end probe ───────────────────────────────────────────────────────
    def run(self, task: BenchmarkTask, steps: list[tuple[str, Any | None]]) -> BenchmarkRunResult:
        """Run a label-addressed action plan and score it.

        ``steps`` is a list of ``(affordance_label, value)`` the agent intends to
        perform; each label is resolved against the freshly-observed PAM. This is
        the minimal driver needed to measure cross-env generalization without
        pulling in the full Continuous Interaction Manager.
        """
        start = perf_counter()
        self.reset(task)
        backend_counts: dict[str, int] = {}
        executed = 0
        failure: str | None = None

        for label, value in steps[: task.max_steps]:
            pam = self.observe(task)
            affordance = pam.find_by_label(label)
            if affordance is None:
                failure = f"affordance not found for label: {label!r}"
                break
            result = self.act(affordance, value=value)
            backend_counts[result.backend_used] = backend_counts.get(result.backend_used, 0) + 1
            executed += 1
            if not result.success:
                failure = result.failure_reason or "execution failed"
                break

        solved = failure is None and self.is_solved(task)
        return BenchmarkRunResult(
            env=task.env,
            task_id=task.task_id,
            success=solved,
            steps=executed,
            latency_ms=round((perf_counter() - start) * 1000.0, 3),
            backend_counts=backend_counts,
            failure_reason=failure,
        )
