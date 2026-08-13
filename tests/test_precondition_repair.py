import asyncio

from src.contracts.types import Affordance, ExecutionResult, Observation, ObservedAssertion
from src.perception.browser_obstruction import observe_browser_obstruction
from src.runtime.action_context import FailureContext, build_action_context
from src.runtime.affordance_controller import AffordanceController
from src.runtime.cognitive_map import CognitiveMap, RuntimeAffordance
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.live_observation import LiveRuntimeObservation, bind_live_observation_to_request
from src.runtime.state_machine import RuntimeOutcome, RuntimeState
from src.runtime.system2_planner import System2Planner


def _runtime_affordance(
    affordance_id: str,
    *,
    entity_id: str,
    grounding: dict,
    confidence: float = 0.9,
) -> RuntimeAffordance:
    grounding = {"safety_level": "low", **grounding}
    return RuntimeAffordance(
        id=affordance_id,
        source="dom",
        entity_id=entity_id,
        action_name="click",
        action_type="button",
        confidence=confidence,
        grounding=grounding,
    )


def _failure_context(target: RuntimeAffordance) -> FailureContext:
    return FailureContext(
        failed_action="click",
        failed_affordance_id=target.id,
        failed_entity_id=target.entity_id,
        expected_effect="oracle.done == true",
        failure_boundary="recoverable_execution_failure",
        failure_type="postcondition_failed",
        reason="blocked",
        transition_id="episode:transition-0001",
        observation_state_id="state-fresh",
    )


def test_existing_agent_planner_uses_explicit_relation_not_label_or_selector():
    target = _runtime_affordance("target-17", entity_id="goal-control", grounding={"selector": "#random"})
    repair = _runtime_affordance(
        "fresh-control-91",
        entity_id="blocker",
        grounding={
            "selector": ".layout-dependent-value",
            "label": "previously unseen wording",
            "recovery_role": "clear_obstruction",
            "remediates": "target-17",
            "recovery_postcondition": "interaction_obstruction.present == false",
            "recovery_safe": True,
            "idempotent": True,
            "irreversible": False,
        },
    )
    cognitive_map = CognitiveMap(task_id="generic")
    cognitive_map.add_affordance(target)
    cognitive_map.add_affordance(repair)

    context = build_action_context(
        cognitive_map,
        request_type="goal_spec",
        failure=_failure_context(target),
    )
    plan = System2Planner(AffordanceController()).plan(context, goal_id="generic_goal", goal_state="oracle.done")

    assert not plan.requires_escalation
    assert plan.actions[0].affordance_id == repair.id
    assert plan.actions[0].expected_effect == "interaction_obstruction.present == false"


def test_existing_agent_planner_refuses_unrelated_unsafe_or_unverifiable_controls():
    target = _runtime_affordance("target", entity_id="goal-control", grounding={})
    candidates = [
        _runtime_affordance(
            "unrelated",
            entity_id="blocker",
            grounding={
                "recovery_role": "clear_obstruction",
                "remediates": "some-other-target",
                "recovery_postcondition": "interaction_obstruction.present == false",
                "recovery_safe": True,
                "idempotent": True,
            },
        ),
        _runtime_affordance(
            "unsafe",
            entity_id="blocker",
            grounding={
                "recovery_role": "clear_obstruction",
                "remediates": "target",
                "recovery_postcondition": "interaction_obstruction.present == false",
                "recovery_safe": False,
                "idempotent": True,
            },
        ),
        _runtime_affordance(
            "unverifiable",
            entity_id="blocker",
            grounding={
                "recovery_role": "clear_obstruction",
                "remediates": "target",
                "recovery_safe": True,
                "idempotent": True,
            },
        ),
    ]
    cognitive_map = CognitiveMap(task_id="refuse")
    cognitive_map.add_affordance(target)
    for candidate in candidates:
        cognitive_map.add_affordance(candidate)

    plan = System2Planner(AffordanceController()).plan(
        build_action_context(cognitive_map, request_type="goal_spec", failure=_failure_context(target)),
        goal_id="generic_goal",
        goal_state="oracle.done",
    )
    assert plan.requires_escalation
    assert plan.actions[0].action == "ask_user"


class _RawBrowserScan:
    def __init__(self, controls):
        self.controls = controls

    def evaluate(self, expression, arg=None):
        _ = expression, arg
        return {
            "targetExists": True,
            "blocked": True,
            "blocker": {"tag": "dialog", "zIndex": "100"},
            "controls": self.controls,
        }


def test_browser_obstruction_accepts_unseen_wording_with_structural_dismiss_evidence():
    observation = asyncio.run(
        observe_browser_obstruction(
            _RawBrowserScan(
                [
                    {
                        "selector": "main > dialog > button:nth-of-type(1)",
                        "label": "Carry on with browsing",
                        "visible": True,
                        "enabled": True,
                        "methodDialog": False,
                        "dismissAttribute": True,
                    }
                ]
            ),
            target_selector="[data-random-target='812']",
        )
    )

    assert observation.blocked
    assert len(observation.controls) == 1
    assert observation.controls[0].confidence == 0.98
    affordance = observation.recovery_affordances(target_affordance_id="unseen-target")[0]
    assert affordance.locator["remediates"] == "unseen-target"


def test_browser_obstruction_refuses_ambiguous_or_destructive_controls():
    observation = asyncio.run(
        observe_browser_obstruction(
            _RawBrowserScan(
                [
                    {
                        "selector": "#option-a",
                        "label": "Option A",
                        "visible": True,
                        "enabled": True,
                        "methodDialog": False,
                        "dismissAttribute": False,
                    },
                    {
                        "selector": "#buy",
                        "label": "Purchase now",
                        "visible": True,
                        "enabled": True,
                        "methodDialog": False,
                        "dismissAttribute": False,
                    },
                ]
            ),
            target_selector="#any-target",
        )
    )

    assert observation.controls == []
    assert observation.recovery_affordances(target_affordance_id="target") == []


def test_browser_obstruction_refuses_a_single_unknown_control_without_dismiss_evidence():
    observation = asyncio.run(
        observe_browser_obstruction(
            _RawBrowserScan(
                [
                    {
                        "selector": "#mystery",
                        "label": "Unseen neutral wording",
                        "visible": True,
                        "enabled": True,
                        "methodDialog": False,
                        "dismissAttribute": False,
                    }
                ]
            ),
            target_selector="#target",
        )
    )

    assert observation.controls == []


def _contract_affordances(*, include_repair: bool) -> list[Affordance]:
    target = Affordance(
        id="observed-target",
        source="DOM",
        type="button",
        label="Complete",
        action="click",
        locator={
            "selector": "[data-changing-target]",
            "entity_id": "oracle",
            "completion_for": "generic_goal",
            "achieves": "oracle.done == true",
        },
        confidence=0.95,
    )
    if not include_repair:
        return [target]
    repair = Affordance(
        id="freshly-observed-repair",
        source="DOM",
        type="button",
        label="Arbitrary holdout label",
        action="click",
        locator={
            "selector": "dialog button:nth-of-type(1)",
            "entity_id": "interaction_obstruction",
            "recovery_role": "clear_obstruction",
            "remediates": "observed-target",
            "recovery_postcondition": "interaction_obstruction.present == false",
            "recovery_safe": True,
            "idempotent": True,
            "irreversible": False,
        },
        confidence=0.9,
    )
    return [target, repair]


def _live_observation(*, done: bool, obstruction: bool, include_repair: bool) -> LiveRuntimeObservation:
    return LiveRuntimeObservation(
        observation=Observation(
            device_states={"oracle": {"done": done}},
            assertions=[
                ObservedAssertion(
                    entity_id="interaction_obstruction",
                    attribute="present",
                    value=obstruction,
                    source="dom",
                    confidence=1.0,
                )
            ],
        ),
        affordances=_contract_affordances(include_repair=include_repair),
        complete_affordance_snapshot=True,
    )


class _RecoveryExecutor:
    def __init__(self):
        self.calls: list[str] = []
        self.target_attempts = 0

    async def execute(self, skill_call, observation):
        _ = observation
        affordance_id = skill_call.params["affordance_id"]
        self.calls.append(affordance_id)
        if affordance_id == "observed-target":
            self.target_attempts += 1
            success = self.target_attempts > 1
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=success,
                latency_ms=1,
                confidence=1.0 if success else 0.0,
                failure_reason=None if success else "click intercepted by freshly observed element",
            )
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=True,
            latency_ms=1,
            confidence=1.0,
        )


class _RecoveryObservations:
    def __init__(self):
        self.requests: list[ObservationRequest] = []
        self.sequence = [
            _live_observation(done=False, obstruction=True, include_repair=True),
            _live_observation(done=False, obstruction=False, include_repair=False),
            _live_observation(done=True, obstruction=False, include_repair=False),
        ]

    async def observe(self, request):
        self.requests.append(request)
        return bind_live_observation_to_request(self.sequence.pop(0), request_id=request.request_id)


class _RecordingPlanner:
    def __init__(self):
        self.delegate = System2Planner(AffordanceController())
        self.contexts = []

    def plan(self, context, **kwargs):
        self.contexts.append(context)
        return self.delegate.plan(context, **kwargs)


class _ForbiddenInterventionBroker:
    async def request(self, request):
        raise AssertionError(f"autonomous Agent recovery must run before Tier-4 intervention: {request.reason}")


def test_cim_executes_repair_reobserves_and_retries_original_goal_with_linked_transitions():
    executor = _RecoveryExecutor()
    provider = _RecoveryObservations()
    ledger = TransitionLedger()
    planner = _RecordingPlanner()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="generic-repair"),
        observation_provider=provider,
        episode_policy=EpisodePolicy(
            max_steps=6,
            deadline_s=2,
            max_retry_attempts=1,
            max_attempts_per_backend=6,
            require_fresh_observation=True,
        ),
        transition_ledger=ledger,
        system2_planner=planner,
        intervention_broker=_ForbiddenInterventionBroker(),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.recovery_attempted and result.recovery_succeeded
    assert result.final_outcome_verified
    assert result.outcome is RuntimeOutcome.VERIFIED_SUCCESS
    assert result.replan_count == 1
    assert executor.calls == ["observed-target", "freshly-observed-repair", "observed-target"]
    assert len(provider.requests) == 3
    assert [record.recovery_action for record in ledger.records] == [
        "replan",
        "agent_replan",
        "resume_after_replan",
    ]
    assert [record.recovery_tier for record in ledger.records] == [2, 2, 2]
    assert ledger.records[1].recovery_of_transition_id == ledger.records[0].transition_id
    assert ledger.records[2].recovery_of_transition_id == ledger.records[1].transition_id
    assert ledger.records[1].postcondition_passed is True
    assert ledger.records[2].postcondition_passed is True
    assert result.final_verification_transition_id == ledger.records[2].transition_id
    failure_contexts = [context.failure for context in planner.contexts if context.failure is not None]
    assert len(failure_contexts) == 1
    assert failure_contexts[0].failed_affordance_id == "observed-target"
    assert failure_contexts[0].observation_state_id.startswith("state-")
    assert failure_contexts[0].observation_request_id.startswith("request-")


def test_runtime_does_not_replan_from_a_stale_observation():
    executor = _RecoveryExecutor()
    planner = _RecordingPlanner()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="no-fresh-replan"),
        system2_planner=planner,
        episode_policy=EpisodePolicy(max_steps=3, deadline_s=2, require_fresh_observation=False),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert len(planner.contexts) == 1
    assert planner.contexts[0].failure is None
    assert not any(step.get("selected_action") == "replan" for step in result.recovery_trace)


class _PlainObservationProvider:
    async def observe(self, request):
        _ = request
        return Observation(device_states={"oracle": {"done": False}})


def test_plain_state_delta_cannot_authorize_semantic_replanning_from_retained_affordances():
    executor = _RecoveryExecutor()
    planner = _RecordingPlanner()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="partial-observation"),
        observation_provider=_PlainObservationProvider(),
        system2_planner=planner,
        episode_policy=EpisodePolicy(max_steps=3, deadline_s=2),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert len(planner.contexts) == 1
    assert result.replan_count == 0


class _ReplayProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def observe(self, request):
        _ = request
        return self.snapshot


def test_replayed_complete_snapshot_cannot_authorize_replanning():
    snapshot = _live_observation(done=False, obstruction=False, include_repair=False)
    executor = _RecoveryExecutor()
    planner = _RecordingPlanner()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="replayed-snapshot"),
        observation_provider=_ReplayProvider(snapshot),
        system2_planner=planner,
        episode_policy=EpisodePolicy(max_steps=3, deadline_s=2),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            snapshot,
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert len(planner.contexts) == 1
    assert result.replan_count == 0


class _RelabelledStaleProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def observe(self, request):
        return LiveRuntimeObservation(
            observation=self.snapshot.observation,
            affordances=self.snapshot.affordances,
            complete_affordance_snapshot=True,
            response_to_request_id=request.request_id,
            captured_at_ms=request.requested_at_ms - 1,
        )


def test_relabelled_old_capture_cannot_authorize_replanning():
    snapshot = _live_observation(done=False, obstruction=True, include_repair=True)
    executor = _RecoveryExecutor()
    planner = _RecordingPlanner()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="relabelled-stale-capture"),
        observation_provider=_RelabelledStaleProvider(snapshot),
        system2_planner=planner,
        episode_policy=EpisodePolicy(max_steps=3, deadline_s=2),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            _live_observation(done=False, obstruction=False, include_repair=False),
            goal_id="generic_goal",
            goal_state="oracle.done == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.replan_count == 0
    assert executor.calls == ["observed-target"]


def test_recovery_planner_rejects_missing_risk_metadata():
    target = _runtime_affordance("target", entity_id="target", grounding={})
    repair = _runtime_affordance(
        "repair",
        entity_id="obstruction",
        grounding={
            "recovery_role": "clear_obstruction",
            "remediates": "target",
            "recovery_postcondition": "obstruction.present == false",
            "recovery_safe": True,
            "idempotent": True,
            "safety_level": "low",
        },
    )
    cognitive_map = CognitiveMap(task_id="missing-risk")
    cognitive_map.add_affordance(target)
    cognitive_map.add_affordance(repair)

    plan = System2Planner(AffordanceController()).plan(
        build_action_context(cognitive_map, request_type="goal_spec", failure=_failure_context(target)),
        goal_id="goal",
        goal_state="oracle.done == true",
    )

    assert plan.requires_escalation


def test_runtime_enforces_observed_affordance_risk_metadata_before_execution():
    high_risk = Affordance(
        id="dangerous-observed-control",
        source="DOM",
        type="button",
        label="Transfer",
        action="click",
        locator={
            "entity_id": "account",
            "completion_for": "transfer",
            "achieves": "account.transfer_complete == true",
            "irreversible": True,
        },
        confidence=0.99,
        safety_level="high",
    )
    executor = _RecoveryExecutor()
    manager = ContinuousInteractionManager(
        {},
        {"dom": executor},
        CognitiveMap(task_id="risk-metadata"),
    )

    result = asyncio.run(
        manager.run_observed_goal(
            LiveRuntimeObservation(
                observation=Observation(device_states={"account": {"transfer_complete": False}}),
                affordances=[high_risk],
            ),
            goal_id="transfer",
            goal_state="account.transfer_complete == true",
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.failure_type == "unsafe_primitive_action"
    assert executor.calls == []
