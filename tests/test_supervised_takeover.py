"""Tier-4 handovers must be observable, and the metric must not overstate them."""

from __future__ import annotations

import pytest

from src.recovery.human_escalation import HumanEscalationPolicy
from src.recovery.supervised_takeover import (
    SupervisedTakeover,
    TakeoverStateError,
)


class FakeClock:
    """Deterministic milliseconds; no sleeping in tests."""

    def __init__(self) -> None:
        self.now = 1_000

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def _takeover() -> tuple[SupervisedTakeover, FakeClock]:
    clock = FakeClock()
    return SupervisedTakeover(clock=clock), clock


# ── the handover itself ──────────────────────────────────────────────────────────


def test_pause_then_resume_records_the_wait():
    takeover, clock = _takeover()

    takeover.pause("ep-1", "unresolved perceptual conflict")
    assert takeover.is_paused
    clock.advance(2_500)
    record = takeover.resume("re-pointed the agent at the correct button")

    assert not takeover.is_paused
    assert record.wait_ms == 2_500
    assert record.corrected is True


def test_resume_without_a_correction_is_not_counted_as_one():
    """ "Looked and did nothing" must stay distinguishable from "fixed it"."""
    takeover, clock = _takeover()

    takeover.pause("ep-1", "high safety level")
    clock.advance(400)
    record = takeover.resume()

    assert record.corrected is False
    assert record.correction == ""
    assert takeover.metrics()["corrections"] == 0


def test_wait_is_unknown_while_still_paused():
    takeover, _ = _takeover()
    record = takeover.pause("ep-1", "irreversible action")

    assert record.is_open
    assert record.wait_ms is None


# ── ordering guards ──────────────────────────────────────────────────────────────


def test_second_pause_is_rejected_instead_of_losing_the_first():
    takeover, _ = _takeover()
    takeover.pause("ep-1", "conflict")

    with pytest.raises(TakeoverStateError):
        takeover.pause("ep-2", "another")


def test_resume_without_a_pause_is_rejected():
    takeover, _ = _takeover()
    with pytest.raises(TakeoverStateError):
        takeover.resume("nothing was paused")


# ── measurable HITL correction ───────────────────────────────────────────────────


def test_metrics_report_rate_and_wait_over_completed_handovers():
    takeover, clock = _takeover()

    takeover.pause("ep-1", "conflict")
    clock.advance(1_000)
    takeover.resume("adjusted the target")

    takeover.pause("ep-2", "safety")
    clock.advance(3_000)
    takeover.resume()  # supervisor approved without changing anything

    metrics = takeover.metrics()
    assert metrics["pauses"] == 2
    assert metrics["completed"] == 2
    assert metrics["corrections"] == 1
    assert metrics["correction_rate"] == 0.5
    assert metrics["mean_wait_ms"] == 2_000
    assert metrics["total_wait_ms"] == 4_000


def test_open_handover_does_not_skew_the_rate():
    takeover, clock = _takeover()
    takeover.pause("ep-1", "conflict")
    clock.advance(500)
    takeover.resume("fixed")
    takeover.pause("ep-2", "still waiting")  # deliberately left open

    metrics = takeover.metrics()
    assert metrics["pauses"] == 2
    assert metrics["completed"] == 1
    assert metrics["correction_rate"] == 1.0, "an unfinished pause must not count as a non-correction"


def test_empty_takeover_reports_zeroes_not_errors():
    takeover, _ = _takeover()
    assert takeover.metrics() == {
        "pauses": 0,
        "completed": 0,
        "corrections": 0,
        "correction_rate": 0.0,
        "mean_wait_ms": 0.0,
        "total_wait_ms": 0,
    }


# ── wiring to the existing tier-4 decision ───────────────────────────────────────


def test_escalation_decision_drives_a_recorded_handover():
    """The policy says whether to escalate; the takeover makes it observable."""
    from src.contracts.types import RollbackSpec, SkillTuple

    policy = HumanEscalationPolicy()
    skill = SkillTuple(
        skill_id="unlock_door",
        description="Unlock the room door",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["wot"],
        preferred_backends=["wot"],
        rollback=RollbackSpec("lock_door", {"room": "A"}),
        failure_modes={},
        timeout_ms=3000,
        safety_level="high",
        irreversible=True,
    )
    decision = policy.decide(skill)
    assert decision.should_escalate

    takeover, clock = _takeover()
    takeover.pause("ep-42", decision.reason)
    clock.advance(1_200)
    record = takeover.resume("supervisor confirmed the unlock")

    assert record.reason == decision.reason
    assert record.to_dict()["wait_ms"] == 1_200
    assert takeover.metrics()["correction_rate"] == 1.0
