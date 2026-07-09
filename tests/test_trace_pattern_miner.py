from src.adaptation.failure_boundary import FailureBoundary
from src.adaptation.pattern_miner import FailurePatternMiner
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger


def _timeout_event(
    episode_id: str,
    *,
    incident_id: str = "",
    recovered: bool = True,
    backend: str = "wot",
    context_key: str = "smart_room:thermostat",
) -> EpisodeFailureEvent:
    return EpisodeFailureEvent(
        episode_id=episode_id,
        task_id="prepare_room_A",
        skill_id="set_temperature",
        backend=backend,
        failure_type="timeout",
        boundary="immediate_runtime_error",
        context_key=context_key,
        incident_id=incident_id,
        recovery_action="reroute",
        recovery_success=recovered,
        safety_regression=False,
    )


def test_trace_ledger_groups_failures_by_stable_signature_not_raw_episode():
    ledger = TraceLedger()
    ledger.record(_timeout_event("ep_1"))
    ledger.record(_timeout_event("ep_2"))
    ledger.record(_timeout_event("ep_3", backend="dom"))

    groups = ledger.group_by_signature()

    assert sorted(groups) == [
        "set_temperature|dom|timeout|smart_room:thermostat",
        "set_temperature|wot|timeout|smart_room:thermostat",
    ]
    assert [event.episode_id for event in groups["set_temperature|wot|timeout|smart_room:thermostat"]] == [
        "ep_1",
        "ep_2",
    ]


def test_pattern_miner_promotes_cross_episode_recovered_pattern():
    ledger = TraceLedger()
    for index in range(5):
        ledger.record(_timeout_event(f"ep_{index}", recovered=index != 4))

    proposals = FailurePatternMiner(min_support=3, min_recovery_success_rate=0.75).mine(ledger)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.analysis.boundary == FailureBoundary.POLICY_LEARNING_OPPORTUNITY
    assert proposal.signature == "set_temperature|wot|timeout|smart_room:thermostat"
    assert proposal.support == 5
    assert proposal.recovery_success_rate == 0.8
    assert proposal.proposal_type == "backend_reliability_adjustment"


def test_pattern_miner_does_not_promote_single_incident_outage():
    ledger = TraceLedger()
    for index in range(5):
        ledger.record(_timeout_event(f"ep_{index}", incident_id="wot_server_outage_1"))

    proposals = FailurePatternMiner(min_support=3, min_distinct_incidents=2).mine(ledger)

    assert proposals == []


def test_pattern_miner_blocks_policy_candidate_when_safety_regressed():
    ledger = TraceLedger()
    for index in range(4):
        ledger.record(_timeout_event(f"ep_{index}", recovered=True))
    ledger.record(_timeout_event("ep_unsafe", recovered=True))
    ledger.events[-1].safety_regression = True

    proposals = FailurePatternMiner(min_support=3).mine(ledger)

    assert proposals == []
