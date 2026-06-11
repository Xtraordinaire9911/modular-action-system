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
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
