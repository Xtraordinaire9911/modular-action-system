"""Abstract base for all backend executors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.contracts.types import ExecutionResult, Observation, SkillCall


class ExecutorBase(ABC):
    """All executors must implement execute() and probe_availability()."""

    @abstractmethod
    async def execute(
        self,
        skill_call: SkillCall,
        observation: Observation,
    ) -> ExecutionResult:
        """Execute *skill_call* in the context of *observation*.

        Returns an ExecutionResult regardless of success/failure.
        Must not raise; capture exceptions and surface them in
        ExecutionResult.failure_reason.
        """

    @abstractmethod
    async def probe_availability(self) -> bool:
        """Return True if the backend is reachable right now."""
