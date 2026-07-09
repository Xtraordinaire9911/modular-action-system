import json

from src.adaptation.artifact_writer import write_adaptation_artifacts
from src.adaptation.pattern_miner import FailurePatternMiner
from src.adaptation.trace_ledger import EpisodeFailureEvent, TraceLedger


def _event(episode_id: str, recovered: bool = True) -> EpisodeFailureEvent:
    return EpisodeFailureEvent(
        episode_id=episode_id,
        task_id="prepare_room_A",
        skill_id="set_temperature",
        backend="wot",
        failure_type="timeout",
        boundary="immediate_runtime_error",
        context_key="smart_room:thermostat",
        recovery_action="reroute",
        recovery_success=recovered,
    )


def test_trace_ledger_round_trips_jsonl(tmp_path):
    path = tmp_path / "trace_ledger.jsonl"
    ledger = TraceLedger()
    ledger.record(_event("ep_001"))
    ledger.record(_event("ep_002", recovered=False))

    ledger.write_jsonl(path)
    loaded = TraceLedger.read_jsonl(path)

    assert [event.episode_id for event in loaded.events] == ["ep_001", "ep_002"]
    assert loaded.events[1].recovery_success is False
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["skill_id"] == "set_temperature"


def test_write_adaptation_report_and_policy_proposals(tmp_path):
    ledger = TraceLedger()
    for index in range(5):
        ledger.record(_event(f"ep_{index}", recovered=index != 4))
    proposals = FailurePatternMiner(min_support=3, min_recovery_success_rate=0.75).mine(ledger)

    paths = write_adaptation_artifacts(ledger, proposals, tmp_path)

    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    policy = json.loads(paths["policy_proposals"].read_text(encoding="utf-8"))

    assert paths["ledger"].name == "trace_ledger.jsonl"
    assert report["summary"] == {
        "total_failure_events": 5,
        "pattern_candidates": 1,
        "policy_proposals": 1,
    }
    assert report["patterns"][0]["signature"] == "set_temperature|wot|timeout|smart_room:thermostat"
    assert report["patterns"][0]["recovery_success_rate"] == 0.8
    assert policy["proposals"][0]["release_gate"]["approved"] is False
    assert "human approval is required" in policy["proposals"][0]["release_gate"]["reasons"]
    assert policy["proposals"][0]["safe_to_auto_apply"] is False
    assert policy["proposals"][0]["needs_human_review"] is True
    assert "safety_non_regression" in policy["proposals"][0]["release_gate"]["required_checks"]
