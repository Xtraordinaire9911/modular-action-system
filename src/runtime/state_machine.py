"""Runtime state labels for the Continuous Interaction Manager."""

from __future__ import annotations

from enum import Enum


class RuntimeState(str, Enum):
    IDLE = "idle"
    PRECHECK = "precheck"
    ROUTING = "routing"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    PAUSING = "pausing"
    AWAITING_HUMAN = "awaiting_human"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


class RuntimeOutcome(str, Enum):
    """Closed terminal result projection; world success remains oracle-owned."""

    VERIFIED_SUCCESS = "verified_success"
    USER_ACTION_REQUIRED = "user_action_required"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNSUPPORTED = "unsupported"
    TERMINAL_FAILURE = "terminal_failure"
