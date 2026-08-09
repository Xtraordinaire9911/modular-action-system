"""Tests for deterministic chaos policy and offline injection."""

import asyncio

from evaluation.chaos_monkey import ChaosEvent, ChaosPolicy, OfflineChaosExecutor, live_hook_for_event
from src.contracts.types import ExecutionResult, Observation, SkillCall


class _OkExecutor:
    async def execute(self, skill_call, observation):
        return ExecutionResult(skill_call.skill_id, "wot", True, 10.0, 1.0, raw_observation_delta={"ok": True})


def test_seeded_policy_is_deterministic():
    left = ChaosPolicy.seeded(42, level=3)
    right = ChaosPolicy.seeded(42, level=3)

    assert left.events == right.events
    assert len(left.events) == 2


def test_offline_executor_injects_before_skill_failure():
    policy = ChaosPolicy(
        seed=1,
        events=[ChaosEvent("e1", "wot_timeout", "wot", "before_skill", skill_id="set_temperature")],
    )
    executor = OfflineChaosExecutor("wot", _OkExecutor(), policy)

    result = asyncio.run(executor.execute(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.success is False
    assert result.failure_reason == "wot_timeout"
    assert executor.events_applied == policy.events


def test_live_hook_maps_to_existing_fault_controls():
    event = ChaosEvent("e1", "dom_selector_mutation", "dom", "before_skill")

    assert live_hook_for_event(event) == {"surface": "dom", "fault": "selector_mutation"}
