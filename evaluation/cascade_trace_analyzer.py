"""Helpers for summarizing white-box recovery traces."""

from __future__ import annotations

from typing import Any

from src.recovery.recovery_cascade import RecoveryTrace


def analyze_recovery_trace(trace: RecoveryTrace) -> dict[str, Any]:
    selected = next((step for step in trace.steps if step.selected), None)
    return {
        "failure_type": trace.failure_type,
        "boundary": trace.boundary,
        "full_cascade_trace": bool(trace.steps) and selected is not None,
        "steps_considered": len([step for step in trace.steps if step.considered]),
        "selected_action": trace.selected_action,
        "selected_tier": trace.selected_tier,
        "selected_policy": selected.policy if selected else "",
        "selected_backend": trace.selected_backend or (selected.backend if selected else ""),
        "selected_reason": selected.reason if selected else "",
    }
