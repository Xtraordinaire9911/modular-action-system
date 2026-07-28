"""End-to-end episode recovery execution tests."""

import asyncio

from src.contracts.types import Condition, ExecutionResult, Observation, RollbackSpec, SkillCall, SkillTuple
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.episode import CancellationToken, EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.state_machine import RuntimeState


class _SequenceExecutor:
    def __init__(self, backend: str, outcomes):
        self.backend = backend
        self.outcomes = list(outcomes)
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call, observation):
        self.calls.append(skill_call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _SequenceObservationProvider:
    def __init__(self, observations):
        self.observations = list(observations)
        self.requests: list[ObservationRequest] = []

    async def observe(self, request):
        self.requests.append(request)
        return self.observations.pop(0)


def _skill(
    *,
    allowed=("wot",),
    preferred=("wot",),
    rollback=None,
    idempotent=False,
):
    return SkillTuple(
        skill_id="set_temperature",
        description="Set target temperature",
        parameters_schema={},
        preconditions=[],
        postconditions=[Condition("thermostat.temperature == params.target")],
        allowed_backends=list(allowed),
        preferred_backends=list(preferred),
        rollback=rollback,
        failure_modes={},
        timeout_ms=100,
        safety_level="low",
        irreversible=False,
        idempotent=idempotent,
    )


def _result(backend, *, success, value=None, failure_reason=None):
    delta = {"thermostat": {"temperature": value}} if value is not None else {}
    return ExecutionResult(
        skill_id="set_temperature",
        backend_used=backend,
        success=success,
        latency_ms=3,
        confidence=1.0 if success else 0.0,
        failure_reason=failure_reason,
        raw_observation_delta=delta,
    )


def test_idempotent_timeout_is_retried_and_verified_from_fresh_observation():
    executor = _SequenceExecutor(
        "wot",
        [
            _result("wot", success=False, failure_reason="timeout"),
            _result("wot", success=True, value=22),
        ],
    )
    provider = _SequenceObservationProvider(
        [
            Observation(device_states={"thermostat": {"temperature": 20}}),
            Observation(device_states={"thermostat": {"temperature": 22}}),
        ]
    )
    ledger = TransitionLedger()
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill(idempotent=True)},
        {"wot": executor},
        CognitiveMap(task_id="retry"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(max_steps=4, deadline_s=2, max_retry_attempts=2),
        transition_ledger=ledger,
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert result.recovery_attempted and result.recovery_succeeded
    assert result.final_outcome_verified
    assert result.attempts == 2
    assert len(executor.calls) == 2
    assert len(provider.requests) == 2
    assert [record.recovery_action for record in ledger.records] == ["retry", "retry"]
    assert ledger.records[0].recovery_of_transition_id == ""
    assert ledger.records[1].recovery_of_transition_id == ledger.records[0].transition_id
    assert len(manager.failure_ledger.events) == 1
    failure_event = manager.failure_ledger.events[0]
    assert failure_event.transition_id == result.transition_ids[0]
    assert failure_event.state_id_before
    assert failure_event.state_id_after
    assert failure_event.recovery_success is True


def test_failed_retry_keeps_retry_label_before_next_reroute_transition():
    wot = _SequenceExecutor(
        "wot",
        [
            _result("wot", success=False, failure_reason="timeout"),
            _result("wot", success=False, failure_reason="timeout"),
        ],
    )
    visual = _SequenceExecutor("visual", [_result("visual", success=True, value=22)])
    provider = _SequenceObservationProvider(
        [
            Observation(device_states={"thermostat": {"temperature": 20}}),
            Observation(device_states={"thermostat": {"temperature": 20}}),
            Observation(device_states={"thermostat": {"temperature": 22}}),
        ]
    )
    ledger = TransitionLedger()
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill(allowed=("wot", "visual"), preferred=("wot",), idempotent=True)},
        {"wot": wot, "visual": visual},
        CognitiveMap(task_id="retry-then-reroute"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(
            max_steps=5,
            deadline_s=2,
            max_retry_attempts=1,
            max_attempts_per_backend=2,
        ),
        transition_ledger=ledger,
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert [record.backend for record in ledger.records] == ["wot", "wot", "visual"]
    assert [record.recovery_action for record in ledger.records] == ["retry", "retry", "reroute"]
    assert ledger.records[0].recovery_of_transition_id == ""
    assert ledger.records[1].recovery_of_transition_id == ledger.records[0].transition_id
    assert ledger.records[2].recovery_of_transition_id == ledger.records[1].transition_id


def test_episode_policy_can_disable_retries_even_when_cascade_default_allows_them():
    executor = _SequenceExecutor(
        "wot",
        [
            _result("wot", success=False, failure_reason="timeout"),
            _result("wot", success=True, value=22),
        ],
    )
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill(idempotent=True)},
        {"wot": executor},
        CognitiveMap(task_id="no-retry"),
        episode_policy=EpisodePolicy(max_steps=4, deadline_s=2, max_retry_attempts=0),
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.ESCALATED
    assert result.attempts == 1
    assert len(executor.calls) == 1
    retry_step = next(step for step in result.recovery_trace if step["policy"] == "retry")
    assert retry_step["selected"] is False
    assert retry_step["reason"] == "episode retry budget exhausted"


def test_episode_policy_can_raise_retry_budget_above_cascade_default():
    executor = _SequenceExecutor(
        "wot",
        [
            _result("wot", success=False, failure_reason="timeout"),
            _result("wot", success=False, failure_reason="timeout"),
            _result("wot", success=True, value=22),
        ],
    )
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill(idempotent=True)},
        {"wot": executor},
        CognitiveMap(task_id="two-retries"),
        episode_policy=EpisodePolicy(
            max_steps=4,
            deadline_s=2,
            max_retry_attempts=2,
            max_attempts_per_backend=3,
        ),
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert result.attempts == 3
    assert len(executor.calls) == 3


def test_non_retryable_dom_failure_executes_visual_reroute():
    dom = _SequenceExecutor("dom", [_result("dom", success=False, failure_reason="selector_not_found")])
    visual = _SequenceExecutor("visual", [_result("visual", success=True, value=22)])
    ledger = TransitionLedger()
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill(allowed=("dom", "visual"), preferred=("dom",))},
        {"dom": dom, "visual": visual},
        CognitiveMap(task_id="reroute"),
        transition_ledger=ledger,
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert result.selected_backend == "visual"
    assert result.recovery_tier == 2
    assert result.recovery_succeeded and result.final_outcome_verified
    assert len(dom.calls) == 1
    assert len(visual.calls) == 1
    assert [record.backend for record in ledger.records] == ["dom", "visual"]
    assert ledger.records[0].recovery_of_transition_id == ""
    assert ledger.records[1].recovery_action == "reroute"
    assert ledger.records[1].recovery_of_transition_id == ledger.records[0].transition_id


def test_postcondition_failure_executes_and_verifies_rollback():
    executor = _SequenceExecutor(
        "wot",
        [
            _result("wot", success=True, value=25),
            _result("wot", success=True, value=20),
        ],
    )
    skill = _skill(rollback=RollbackSpec("set_temperature", {"target": 20}))
    ledger = TransitionLedger()
    manager = ContinuousInteractionManager(
        {"set_temperature": skill},
        {"wot": executor},
        CognitiveMap(task_id="rollback"),
        transition_ledger=ledger,
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.FAILED
    assert result.recovery_tier == 3
    assert result.recovery_attempted and result.recovery_succeeded
    assert not result.final_outcome_verified
    assert [call.params["target"] for call in executor.calls] == [22, 20]
    assert ledger.records[0].execution_success is True
    assert ledger.records[0].postcondition_passed is False
    assert ledger.records[0].failure_reason == "postcondition_failed"
    assert ledger.records[-1].recovery_action == "rollback"
    assert ledger.records[-1].recovery_of_transition_id == ledger.records[0].transition_id
    assert ledger.records[-1].reversible_result is True


def test_human_cancellation_stops_episode_before_executor_call():
    token = CancellationToken()
    token.cancel("operator stopped task")
    executor = _SequenceExecutor("wot", [_result("wot", success=True, value=22)])
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill()},
        {"wot": executor},
        CognitiveMap(task_id="cancelled"),
        cancellation_token=token,
    )

    result = asyncio.run(manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.state == RuntimeState.ESCALATED
    assert result.reason == "operator stopped task"
    assert executor.calls == []
