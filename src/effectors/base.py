"""Executor base contract (Member B).

Every backend executor (DOM, WoT, Visual) takes one ``Affordance`` plus an
optional value and returns a standardised ``ExecutionResult``. This is the
single execution interface the runtime (Member C) drives, regardless of which
modality actually performs the action — the core of the "interface-agnostic at
the skill level, interface-aware only at execution level" principle.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from src.contracts.types import Affordance, ExecutionResult


class ExecutorBase(ABC):
    """Abstract backend effector. Subclasses set ``backend`` and implement ``_run``."""

    backend: str = "abstract"

    @abstractmethod
    def _run(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        """Perform the primitive. Return an observation delta dict on success.

        Raise any exception to signal failure; ``execute`` converts it into a
        failed ``ExecutionResult`` with the message as ``failure_reason``.
        """

    def execute(
        self,
        affordance: Affordance,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult:
        """Time the primitive and wrap success/failure into an ExecutionResult."""
        start = time.perf_counter()
        try:
            delta = self._run(affordance, value)
            latency = (time.perf_counter() - start) * 1000.0
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=True,
                latency_ms=round(latency, 3),
                confidence=affordance.confidence,
                failure_reason=None,
                raw_observation_delta=delta or {},
            )
        except Exception as exc:  # noqa: BLE001 — convert all failures to a result
            latency = (time.perf_counter() - start) * 1000.0
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=False,
                latency_ms=round(latency, 3),
                confidence=affordance.confidence,
                failure_reason=f"{type(exc).__name__}: {exc}",
                raw_observation_delta={},
            )
