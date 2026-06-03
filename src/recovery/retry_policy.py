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

    def decide(self, result: ExecutionResult, attempt: int) -> RetryDecision:
        transient = result.failure_reason in {
            "timeout",
            "visual_confidence_low",
            "backend_busy",
        } or (result.failure_reason or "").startswith("HTTP 5")
        should_retry = (not result.success) and transient and attempt < self.max_attempts
        delay = self.base_delay_s * (2 ** max(attempt - 1, 0)) if should_retry else 0.0
        reason = "transient failure" if should_retry else "retry not applicable"
        return RetryDecision(should_retry=should_retry, next_attempt=attempt + 1, delay_s=delay, reason=reason)
