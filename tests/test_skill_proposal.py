"""Review-gated skill proposal mining tests."""

import json

from src.adaptation.skill_proposal import SkillProposalMiner, write_skill_proposals
from src.runtime.episode import TransitionLedger, TransitionRecord


def _record(episode, step, *, success=True, recovery_action=""):
    action = "type" if step == 1 else "click"
    return TransitionRecord(
        task_id="booking",
        episode_id=episode,
        transition_id=f"{episode}:t{step}",
        step=step,
        state_id_before="state-start" if step == 1 else "state-form-filled",
        state_id_after="state-form-filled" if step == 1 else "state-booked",
        skill_id="reserve_room",
        affordance_key="dom:room" if step == 1 else "dom:confirm",
        backend="dom",
        params={
            "primitive_action": action,
            "affordance_id": f"a{step}",
            "room": "A",
        },
        success=success,
        execution_success=success,
        postcondition_passed=success,
        latency_ms=2,
        attempt=1,
        observation_delta={},
        recovery_action=recovery_action,
    )


def test_repeated_verified_chain_produces_review_only_candidate(tmp_path):
    ledger = TransitionLedger()
    for episode in ("ep1", "ep2", "ep3"):
        ledger.record(_record(episode, 1))
        ledger.record(_record(episode, 2))

    proposals = SkillProposalMiner(min_support=3, min_steps=2).mine(ledger)
    path = write_skill_proposals(proposals, tmp_path / "candidate_skills.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert len(proposals) == 1
    assert proposals[0].support == 3
    assert [step.action for step in proposals[0].steps] == ["type", "click"]
    assert proposals[0].parameters_schema == {"room": {"type": "string"}}
    assert proposals[0].safe_to_auto_apply is False
    assert proposals[0].needs_human_review is True
    assert payload["auto_apply"] is False


def test_failed_or_recovered_chain_is_not_silently_internalized():
    ledger = TransitionLedger()
    for episode in ("ep1", "ep2", "ep3"):
        ledger.record(_record(episode, 1))
        ledger.record(_record(episode, 2, recovery_action="reroute"))

    assert SkillProposalMiner().mine(ledger) == []
