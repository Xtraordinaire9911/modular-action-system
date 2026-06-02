"""Unit tests for the backend confidence tracker and router."""

import pytest
from src.backend_router.backend_confidence import BackendConfidenceTracker
from src.backend_router.router import BackendRouter, RoutingDecision
from src.contracts.types import Condition, SkillCall, SkillTuple


def _make_skill_tuple(allowed: list[str], preferred: list[str]) -> SkillTuple:
    return SkillTuple(
        skill_id="set_temperature",
        description="test",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=allowed,
        preferred_backends=preferred,
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
    )


def _make_skill_call() -> SkillCall:
    return SkillCall(skill_id="set_temperature", params={"room": "A", "target": 22})


# ── BackendConfidenceTracker ──────────────────────────────────────────────────

class TestBackendConfidenceTracker:
    def test_initial_stats_are_optimistic(self):
        tracker = BackendConfidenceTracker()
        stats = tracker.get_stats("wot")
        assert stats.reliability == 1.0
        assert stats.latency > 0

    def test_success_keeps_reliability_high(self):
        tracker = BackendConfidenceTracker()
        for _ in range(5):
            tracker.record("wot", success=True, latency_ms=100)
        assert tracker.get_stats("wot").reliability > 0.9

    def test_failures_lower_reliability(self):
        tracker = BackendConfidenceTracker()
        for _ in range(10):
            tracker.record("wot", success=False, latency_ms=100)
        assert tracker.get_stats("wot").reliability < 0.5

    def test_latency_ema_converges(self):
        tracker = BackendConfidenceTracker()
        for _ in range(20):
            tracker.record("dom", success=True, latency_ms=50)
        assert tracker.get_stats("dom").latency < 150


# ── BackendRouter ─────────────────────────────────────────────────────────────

class TestBackendRouter:
    def test_selects_from_available(self):
        router = BackendRouter()
        st = _make_skill_tuple(["dom", "wot", "visual"], ["wot"])
        decision = router.route(_make_skill_call(), st, available=["dom", "wot"])
        assert decision.selected_backend in ("dom", "wot")

    def test_excludes_backends_correctly(self):
        router = BackendRouter()
        st = _make_skill_tuple(["dom", "wot"], ["wot"])
        decision = router.route(_make_skill_call(), st, available=["dom", "wot"], exclude=["wot"])
        assert decision.selected_backend == "dom"

    def test_no_candidates_returns_empty(self):
        router = BackendRouter()
        st = _make_skill_tuple(["wot"], ["wot"])
        decision = router.route(_make_skill_call(), st, available=["dom"])
        assert decision.selected_backend == ""
        assert "no available backend" in decision.routing_reason

    def test_repeated_failures_shift_routing_away_from_failed_backend(self):
        router = BackendRouter()
        for _ in range(15):
            router.record_outcome("wot", success=False, latency_ms=500)
        st = _make_skill_tuple(["dom", "wot"], ["wot"])
        decision = router.route(_make_skill_call(), st, available=["dom", "wot"])
        assert decision.selected_backend == "dom"

    def test_routing_decision_has_confidence(self):
        router = BackendRouter()
        st = _make_skill_tuple(["dom"], ["dom"])
        decision = router.route(_make_skill_call(), st, available=["dom"])
        assert 0.0 <= decision.confidence_score <= 1.0
