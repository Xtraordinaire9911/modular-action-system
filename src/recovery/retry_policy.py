"""Tier 1 recovery: retry the same backend."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.types import ExecutionResult


@dataclass
class RetryDecision:
    should_retry: bool
    next_attempt: int
    delay_s: float
    reason: str


class RetryPolicy:
    def __init__(self, max_attempts: int = 2, base_delay_s: float = 0.1) -> None:
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s

    def decide(
        self,
        result: ExecutionResult,
        attempt: int,
        *,
        max_retries: int | None = None,
    ) -> RetryDecision:
        failure = (result.failure_reason or "").strip().lower()
        transient = failure in {
            "timeout",
            "visual_confidence_low",
            "backend_busy",
        } or failure.startswith("http 5")
        transient = transient or "timeout" in failure or "timed out" in failure
        retry_count = max(attempt - 1, 0)
        within_budget = retry_count < max_retries if max_retries is not None else attempt < self.max_attempts
        should_retry = (not result.success) and transient and within_budget
        delay = self.base_delay_s * (2 ** max(attempt - 1, 0)) if should_retry else 0.0
        if should_retry:
            reason = "transient failure"
        elif transient and max_retries is not None and not within_budget:
            reason = "episode retry budget exhausted"
        else:
            reason = "retry not applicable"
        return RetryDecision(should_retry=should_retry, next_attempt=attempt + 1, delay_s=delay, reason=reason)
