import json

import yaml

from evaluation.cascade_trace_analyzer import analyze_recovery_trace
from evaluation.longitudinal_eval import LongitudinalEvalInput, compare_policy_runs
from evaluation.policy_proposal import build_policy_proposals
from scripts.apply_policy_proposal import dry_run_policy_application
from src.adaptation.adaptation_gate import AdaptationGate
from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary
from src.adaptation.pattern_miner import FailurePatternMiner
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger
from src.recovery.recovery_cascade import RecoveryDecisionStep, RecoveryTrace
from src.runtime.affordance_controller import AffordanceController
from src.runtime.system2_planner import System2Planner


def _ledger_with_pattern() -> TraceLedger:
    ledger = TraceLedger()
    for index in range(4):
        ledger.record(
            EpisodeFailureEvent(
                episode_id=f"ep_{index}",
                task_id="prepare_room_A",
                skill_id="set_temperature",
                backend="wot",
                failure_type="timeout",
                boundary="immediate_runtime_error",
                context_key="smart_room:thermostat",
                recovery_action="reroute",
                recovery_success=True,
                incident_id=f"incident_{index}",
            )
        )
    return ledger


def test_system2_planner_delegates_to_bounded_affordance_controller():
    planner = System2Planner(controller=AffordanceController())

    assert planner.mode == "deterministic_reflex"
    assert planner.uses_llm is False


def test_adaptation_gate_blocks_unsafe_and_requires_human_review_for_policy_learning():
    gate = AdaptationGate()
    unsafe = FailureAnalysis(
        boundary=FailureBoundary.UNSAFE_GOVERNANCE_BOUNDARY,
        failure_type="unresolved_conflict",
        immediate_action="block_or_human_approval",
        long_term_action="do_not_auto_learn",
    )
    policy = FailureAnalysis(
        boundary=FailureBoundary.POLICY_LEARNING_OPPORTUNITY,
        failure_type="timeout",
        immediate_action="keep_using_recovery_cascade",
        long_term_action="propose_policy_update",
    )

    assert gate.evaluate(unsafe).allowed is False
    policy_decision = gate.evaluate(policy)
    assert policy_decision.allowed is True
    assert policy_decision.needs_human_review is True


def test_cascade_trace_analyzer_reports_coverage_and_selected_step():
    trace = RecoveryTrace(
        failure_type="timeout",
        boundary="immediate_runtime_error",
        steps=[
            RecoveryDecisionStep(1, "retry", True, False, "budget exhausted"),
            RecoveryDecisionStep(2, "reroute", True, True, "dom available", "dom"),
        ],
        selected_action="reroute",
        selected_tier=2,
        selected_backend="dom",
    )

    analysis = analyze_recovery_trace(trace)

    assert analysis["full_cascade_trace"] is True
    assert analysis["selected_policy"] == "reroute"
    assert analysis["selected_backend"] == "dom"


def test_policy_proposal_builder_and_dry_run_application_are_passive(tmp_path):
    proposals = FailurePatternMiner(min_support=3, min_distinct_incidents=2).mine(_ledger_with_pattern())
    policy = build_policy_proposals(proposals)
    runtime_policy = tmp_path / "runtime_policy.yaml"
    runtime_policy.write_text("backend_reliability: {}\n", encoding="utf-8")

    dry_run = dry_run_policy_application(policy["proposals"][0], runtime_policy)

    assert policy["proposals"][0]["change_type"] == "backend_reliability_adjustment"
    assert dry_run["would_apply"] is False
    assert dry_run["requires_human_review"] is True
    assert runtime_policy.read_text(encoding="utf-8") == "backend_reliability: {}\n"


def test_longitudinal_eval_computes_gain_retention_and_efficiency():
    result = compare_policy_runs(
        LongitudinalEvalInput(
            before_normal_success_rate=1.0,
            after_normal_success_rate=0.95,
            before_heldout_success_rate=0.4,
            after_heldout_success_rate=0.7,
            safety_regressions=0,
            proposal_count=2,
        )
    )

    assert result["HeldOutGain"] == 0.3
    assert result["BackwardRetention"] == 0.95
    assert result["SafetyNonRegression"] == 1.0
    assert result["ImprovementEfficiency"] == 0.15


def test_runtime_policy_config_exists_and_disables_auto_apply():
    with open("config/runtime_policy.yaml", encoding="utf-8") as handle:
        policy = yaml.safe_load(handle)

    assert policy["adaptation"]["auto_apply"] is False
    assert "conflict_halt_bypass" in policy["adaptation"]["forbidden_change_types"]


def test_policy_proposal_json_is_serializable():
    proposals = FailurePatternMiner(min_support=3).mine(_ledger_with_pattern())
    payload = build_policy_proposals(proposals)

    assert json.loads(json.dumps(payload))["proposals"]
