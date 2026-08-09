from src.adaptation.release_gate import ReleaseGate, ReleaseGateInput


def _gate_input(human_approved: bool = False, **checks: bool) -> ReleaseGateInput:
    values = {
        "normal_suite_passes": True,
        "failure_suite_non_regression": True,
        "safety_non_regression": True,
        "conflict_halt_still_blocks_system1": True,
        "held_out_cases_improve_or_hold": True,
        "cost_within_budget": True,
    }
    values.update(checks)
    return ReleaseGateInput(
        proposal_id="policy_001",
        change_type="backend_reliability_adjustment",
        checks=values,
        human_approved=human_approved,
    )


def test_release_gate_blocks_policy_proposal_without_human_approval():
    result = ReleaseGate().evaluate(_gate_input())

    assert result.approved is False
    assert "human approval is required" in result.reasons
    assert result.safe_to_apply is False


def test_release_gate_approves_low_risk_policy_when_all_checks_and_human_approval_pass():
    result = ReleaseGate().evaluate(_gate_input(human_approved=True))

    assert result.approved is True
    assert result.safe_to_apply is True
    assert result.reasons == []


def test_release_gate_rejects_missing_safety_check_even_with_human_approval():
    result = ReleaseGate().evaluate(
        _gate_input(
            safety_non_regression=False,
            human_approved=True,
        )
    )

    assert result.approved is False
    assert result.safe_to_apply is False
    assert "safety_non_regression failed" in result.reasons


def test_release_gate_never_auto_approves_forbidden_change_types():
    result = ReleaseGate().evaluate(
        ReleaseGateInput(
            proposal_id="policy_unsafe",
            change_type="safety_threshold_lowering",
            checks={
                "normal_suite_passes": True,
                "failure_suite_non_regression": True,
                "safety_non_regression": True,
                "conflict_halt_still_blocks_system1": True,
                "held_out_cases_improve_or_hold": True,
                "cost_within_budget": True,
            },
            human_approved=True,
        )
    )

    assert result.approved is False
    assert "forbidden change type" in result.reasons[0]
