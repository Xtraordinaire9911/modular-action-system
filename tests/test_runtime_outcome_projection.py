import pytest

from src.runtime.continuous_interaction_manager import RuntimeStepResult
from src.runtime.state_machine import RuntimeOutcome, RuntimeState


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            RuntimeStepResult(RuntimeState.COMPLETED, None, final_outcome_verified=True),
            RuntimeOutcome.VERIFIED_SUCCESS,
        ),
        (
            RuntimeStepResult(RuntimeState.ESCALATED, None, failure_type="cancelled"),
            RuntimeOutcome.CANCELLED,
        ),
        (
            RuntimeStepResult(RuntimeState.ESCALATED, None, failure_type="episode_budget_exhausted"),
            RuntimeOutcome.BUDGET_EXHAUSTED,
        ),
        (
            RuntimeStepResult(
                RuntimeState.ESCALATED,
                None,
                failure_type="postcondition_failed",
                user_action_required=True,
            ),
            RuntimeOutcome.USER_ACTION_REQUIRED,
        ),
        (
            RuntimeStepResult(
                RuntimeState.ESCALATED,
                None,
                failure_boundary="architecture_gap",
                failure_type="no_executor_for_affordance_backend",
            ),
            RuntimeOutcome.UNSUPPORTED,
        ),
        (
            RuntimeStepResult(RuntimeState.FAILED, None, failure_type="postcondition_failed"),
            RuntimeOutcome.TERMINAL_FAILURE,
        ),
    ],
)
def test_terminal_outcome_projection_is_closed_and_deterministic(result, expected):
    assert result.outcome is expected


def test_replan_count_uses_handoff_events_not_cascade_trace_duplicates():
    result = RuntimeStepResult(
        RuntimeState.COMPLETED,
        None,
        final_outcome_verified=True,
        recovery_trace=[
            {"policy": "retry", "selected": False, "selected_action": "replan"},
            {"policy": "agent_replan", "selected": True, "selected_action": "replan"},
            {"policy": "agent_replan_boundary", "selected": True, "selected_action": "replan"},
        ],
    )

    assert result.replan_count == 1
