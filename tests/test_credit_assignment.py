from src.adaptation.credit_assignment import assign_credit
from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary


def _analysis(failure_type: str) -> FailureAnalysis:
    return FailureAnalysis(
        boundary=FailureBoundary.RECOVERABLE_EXECUTION_FAILURE,
        failure_type=failure_type,
        evidence=[],
        immediate_action="use_recovery_cascade",
        long_term_action="record_trace_only",
    )


def test_credit_assignment_attributes_timeout_to_backend_unavailability():
    assert assign_credit(_analysis("timeout"), backend="wot") == "backend_unavailability"


def test_credit_assignment_attributes_selector_failure_to_perception_error():
    assert assign_credit(_analysis("selector_failed"), backend="dom") == "perception_error"


def test_credit_assignment_attributes_unknown_skill_to_skill_contract_gap():
    assert assign_credit(_analysis("unknown_skill"), backend="") == "skill_contract_gap"


def test_credit_assignment_prioritizes_unresolved_conflict_as_epistemic_arbitration():
    assert (
        assign_credit(
            _analysis("postcondition_failed"),
            backend="wot",
            unresolved_conflicts=["thermostat temperature mismatch"],
        )
        == "epistemic_arbitration"
    )
