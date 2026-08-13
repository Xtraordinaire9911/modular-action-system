"""Focused tests for the awaited human-intervention boundary."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.runtime.intervention import (
    InMemoryInterventionBroker,
    InterventionAction,
    InterventionDecision,
    InterventionKind,
    InterventionLedger,
    InterventionRecord,
    InterventionRequest,
)


def test_broker_blocks_until_operator_resolves_and_records_latency(tmp_path):
    async def scenario() -> None:
        ledger = InterventionLedger(tmp_path / "interventions.jsonl")
        broker = InMemoryInterventionBroker(ledger, clock_ms=lambda: 1_350)
        request = InterventionRequest(
            episode_id="episode-1",
            task_id="book-room",
            intervention_id="intervention-1",
            kind=InterventionKind.HUMAN_TAKEOVER,
            reason="operator correction required",
            requested_at_ms=1_000,
            state_id="state-before",
            pending_action_fingerprint="click:dom_submit",
            metadata={"source": "tier-4"},
        )

        waiter = asyncio.create_task(broker.request(request))
        pending = await broker.next_request(timeout_s=0.2)

        assert pending == request
        assert not waiter.done()
        assert broker.pending_requests() == [request]

        broker.resolve(
            request.intervention_id,
            InterventionDecision(
                InterventionAction.RESUME,
                actor="fadi",
                note="fixed the room selection",
                correction_applied=True,
                metadata={"control_mode": "human"},
            ),
        )
        decision = await waiter

        assert decision.allows_agent_execution
        assert decision.requires_replan
        assert broker.pending_requests() == []
        assert len(ledger.records) == 1
        record = ledger.records[0]
        assert record.intervention_id == "intervention-1"
        assert record.decision == "resume"
        assert record.actor == "fadi"
        assert record.latency_ms == 350
        assert record.correction_applied
        assert record.metadata == {"source": "tier-4", "control_mode": "human"}

        ledger.mark_resume_evidence(
            request.intervention_id,
            reobserved=True,
            replanned=True,
        )
        persisted = [json.loads(line) for line in (tmp_path / "interventions.jsonl").read_text().splitlines()]
        assert persisted[0]["reobserved"] is True
        assert persisted[0]["replanned"] is True

    asyncio.run(scenario())


def test_approve_and_reject_have_distinct_execution_semantics():
    approved = InterventionDecision(InterventionAction.APPROVE)
    rejected = InterventionDecision(InterventionAction.REJECT)

    assert approved.allows_agent_execution
    assert not approved.requires_replan
    assert not rejected.allows_agent_execution
    assert not rejected.requires_replan


def test_broker_resolves_concurrent_requests_by_id_and_close_cancels_remaining():
    async def scenario() -> None:
        times = iter([2_100, 2_200, 2_300])
        ledger = InterventionLedger()
        broker = InMemoryInterventionBroker(ledger, clock_ms=lambda: next(times))
        first = InterventionRequest(
            episode_id="episode-a",
            intervention_id="intervention-a",
            reason="first pause",
            requested_at_ms=2_000,
        )
        second = InterventionRequest(
            episode_id="episode-b",
            intervention_id="intervention-b",
            reason="second pause",
            requested_at_ms=2_000,
        )
        first_waiter = asyncio.create_task(broker.request(first))
        second_waiter = asyncio.create_task(broker.request(second))
        observed = {
            (await broker.next_request(timeout_s=0.2)).intervention_id,
            (await broker.next_request(timeout_s=0.2)).intervention_id,
        }
        assert observed == {"intervention-a", "intervention-b"}

        broker.resolve("intervention-b", InterventionDecision(InterventionAction.REJECT, actor="operator"))
        assert (await second_waiter).action == InterventionAction.REJECT
        assert not first_waiter.done()

        broker.close("demo shutdown")
        first_decision = await first_waiter
        assert first_decision.action == InterventionAction.CANCEL
        assert first_decision.note == "demo shutdown"
        assert {record.intervention_id for record in ledger.records} == {
            "intervention-a",
            "intervention-b",
        }

        closed_request = InterventionRequest(
            episode_id="episode-c",
            intervention_id="intervention-c",
            reason="request after shutdown",
            requested_at_ms=2_000,
        )
        assert (await broker.request(closed_request)).action == InterventionAction.CANCEL

    asyncio.run(scenario())


def test_cancelled_waiter_is_removed_and_audited():
    async def scenario() -> None:
        ledger = InterventionLedger()
        broker = InMemoryInterventionBroker(ledger, clock_ms=lambda: 5_050)
        request = InterventionRequest(
            episode_id="episode-cancelled",
            intervention_id="intervention-cancelled",
            reason="awaiting a person",
            requested_at_ms=5_000,
        )
        waiter = asyncio.create_task(broker.request(request))
        await broker.next_request(timeout_s=0.2)

        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert broker.pending_requests() == []
        assert ledger.records[0].decision == "cancel"
        assert ledger.records[0].actor == "system"
        assert ledger.records[0].latency_ms == 50

    asyncio.run(scenario())


def test_broker_rejects_unknown_resolution_and_reused_request_id():
    async def scenario() -> None:
        broker = InMemoryInterventionBroker(clock_ms=lambda: 10)
        with pytest.raises(KeyError, match="unknown intervention_id"):
            broker.resolve("missing", InterventionDecision(InterventionAction.REJECT))

        request = InterventionRequest(
            episode_id="episode-duplicate",
            intervention_id="same-id",
            reason="first use",
            requested_at_ms=1,
        )
        waiter = asyncio.create_task(broker.request(request))
        await broker.next_request(timeout_s=0.2)
        broker.resolve(request.intervention_id, InterventionDecision(InterventionAction.REJECT))
        await waiter

        with pytest.raises(ValueError, match="duplicate intervention_id"):
            await broker.request(request)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"episode_id": "", "reason": "pause"}, "episode_id"),
        ({"episode_id": "episode", "reason": ""}, "reason"),
        ({"episode_id": "episode", "reason": "pause", "intervention_id": ""}, "intervention_id"),
        ({"episode_id": "episode", "reason": "pause", "requested_at_ms": -1}, "requested_at_ms"),
    ],
)
def test_request_rejects_invalid_identity_and_timestamp(kwargs, message):
    with pytest.raises(ValueError, match=message):
        InterventionRequest(**kwargs)


def test_ledger_rejects_missing_resume_record():
    ledger = InterventionLedger()

    with pytest.raises(KeyError, match="unknown intervention_id"):
        ledger.mark_resume_evidence("missing", reobserved=True, replanned=True)


def test_ledger_loads_existing_records_before_persisting_new_history(tmp_path):
    path = tmp_path / "interventions.jsonl"
    first = InterventionRecord(
        episode_id="episode-old",
        intervention_id="intervention-old",
        kind="recovery",
        reason="old pause",
        decision="reject",
        actor="operator-a",
        requested_at_ms=100,
        resolved_at_ms=125,
        latency_ms=25,
        metadata={"run": "old"},
    )
    original = InterventionLedger(path)
    original.record(first)

    reopened = InterventionLedger(path)
    assert reopened.records == [first]
    reopened.record(
        InterventionRecord(
            episode_id="episode-new",
            intervention_id="intervention-new",
            kind="human_takeover",
            reason="new pause",
            decision="resume",
            actor="operator-b",
            requested_at_ms=200,
            resolved_at_ms=240,
            latency_ms=40,
            correction_applied=True,
            reobserved=True,
            replanned=True,
            metadata={"run": "new"},
        )
    )

    persisted = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["intervention_id"] for record in persisted] == [
        "intervention-old",
        "intervention-new",
    ]
    assert persisted[0]["metadata"] == {"run": "old"}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("{not-json}\n", "malformed JSON"),
        (json.dumps(["not", "an", "object"]) + "\n", "expected a JSON object"),
        (json.dumps({"episode_id": "missing-fields"}) + "\n", "invalid record shape"),
        (
            json.dumps(
                {
                    "episode_id": "episode-bad",
                    "intervention_id": "intervention-bad",
                    "kind": "not-a-kind",
                    "reason": "pause",
                    "decision": "reject",
                    "actor": "operator",
                    "requested_at_ms": 1,
                    "resolved_at_ms": 2,
                    "latency_ms": 1,
                }
            )
            + "\n",
            "unknown intervention kind",
        ),
    ],
)
def test_ledger_rejects_malformed_existing_records_without_rewriting_file(tmp_path, contents, message):
    path = tmp_path / "malformed-interventions.jsonl"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        InterventionLedger(path)

    assert path.read_text(encoding="utf-8") == contents


def test_ledger_rejects_duplicate_ids_in_existing_history_at_the_later_line(tmp_path):
    path = tmp_path / "duplicate-interventions.jsonl"
    record = InterventionRecord(
        episode_id="episode-duplicate",
        intervention_id="intervention-duplicate",
        kind="recovery",
        reason="pause",
        decision="cancel",
        actor="system",
        requested_at_ms=10,
        resolved_at_ms=20,
        latency_ms=10,
    )
    encoded = json.dumps(record.__dict__, sort_keys=True)
    path.write_text(f"{encoded}\n\n{encoded}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r":3: duplicate intervention_id"):
        InterventionLedger(path)
