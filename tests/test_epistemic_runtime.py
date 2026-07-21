"""Tests for structured CognitiveMap, epistemic arbitration, and demo traces."""

import asyncio
import json

import pytest

from evaluation.integration_eval import write_demo_artifacts
from src.adaptation.llm_judge import LLMJudge
from src.contracts.types import (
    Affordance,
    Condition,
    ExecutionResult,
    Observation,
    RollbackSpec,
    SkillCall,
    SkillTuple,
)
from src.perception.page_affordance_model import PageAffordanceModel
from src.recovery.system2_escalation import System2EscalationPolicy, suggest_system2_decision
from src.runtime.cognitive_map import CognitiveMap, Entity, RuntimeAffordance, StateAssertion
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_observation import observation_from_live_sources
from src.runtime.state_machine import RuntimeState
from src.verification.active_perception import ActivePerceptionResolver, ActivePerceptionResult
from src.verification.conflict_detector import EpistemicArbiter, SemanticConsistencyRule, SensoryConflictError


class _RecordingExecutor:
    def __init__(
        self,
        backend: str,
        result: ExecutionResult | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.backend = backend
        self.calls: list[SkillCall] = []
        self.result = result
        self.exception = exception

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        if self.exception is not None:
            raise self.exception
        return self.result or ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=True,
            latency_ms=1.0,
            confidence=1.0,
            raw_observation_delta={},
        )


class _FakeJudgeClient:
    def complete_json(self, prompt: str):
        assert "ambiguous_false_success" in prompt
        return {
            "boundary": "skill_spec_insufficient",
            "failure_type": "weak_postcondition",
            "confidence": 0.76,
            "evidence": ["reported success is not supported by state evidence"],
            "immediate_action": "use_recovery_cascade",
            "long_term_action": "strengthen_postcondition",
            "safe_to_auto_apply": False,
            "needs_human_review": True,
        }


class _ResolvingProbe:
    async def observe(self, conflicts, cognitive_map, original_observation):
        return Observation(
            device_states={"thermostat_A": {"temperature": 22}},
            accessibility_tree={"page_state": {"thermostat_A": {"temperature": 22}}},
        )


class _UnresolvedProbe:
    async def observe(self, conflicts, cognitive_map, original_observation):
        return None


def _skill_tuple(
    skill_id: str = "set_temperature",
    allowed_backends: list[str] | None = None,
    preferred_backends: list[str] | None = None,
    postconditions: list[Condition] | None = None,
    rollback: RollbackSpec | None = None,
) -> SkillTuple:
    return SkillTuple(
        skill_id=skill_id,
        description=skill_id,
        parameters_schema={},
        preconditions=[],
        postconditions=postconditions or [],
        allowed_backends=allowed_backends or ["wot", "dom", "visual"],
        preferred_backends=preferred_backends or ["wot"],
        rollback=rollback,
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
    )


def test_cognitive_map_stores_state_history_and_affordance_queries():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_entity(Entity(id="thermostat_A", type="thermostat"))
    cmap.add_affordance(
        RuntimeAffordance(
            id="wot_set_temperature",
            source="wot",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="invoke",
            confidence=1.0,
            grounding={"href": "/actions/setTargetTemperature"},
            skill_names=["set_temperature"],
        )
    )
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 20, "dom", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 24, "wot", timestamp_ms=2))

    assert cmap.get_latest_state("thermostat_A", "temperature", source="wot").value == 24
    assert cmap.get_latest_state("thermostat_A", "temperature", source="dom").value == 20
    assert cmap.get_affordances_for_skill("set_temperature")[0].id == "wot_set_temperature"


def test_epistemic_arbiter_computes_numeric_conflict_mass_and_halts():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 20, "dom", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 24, "wot", timestamp_ms=2))
    arbiter = EpistemicArbiter({"temperature": 2.0})

    conflicts = arbiter.check(cmap)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_mass == 2.0
    assert conflicts[0].severity == "high"
    assert arbiter.should_halt_system1(conflicts)


def test_epistemic_arbiter_can_raise_sensory_conflict_error():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("projector_A", "power", "on", "dom", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("projector_A", "power", "off", "wot", timestamp_ms=2))
    arbiter = EpistemicArbiter()

    try:
        arbiter.assert_no_blocking_conflict(cmap)
    except SensoryConflictError as exc:
        assert exc.entity_id == "projector_A"
        assert exc.conflict_mass == 1.0
    else:
        raise AssertionError("expected SensoryConflictError")


def test_system2_payload_generated_for_unresolved_conflict():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 20, "dom", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 24, "wot", timestamp_ms=2))
    EpistemicArbiter({"temperature": 2.0}).check(cmap)

    decision = System2EscalationPolicy().decide(
        skill_id="set_temperature",
        cognitive_map=cmap,
        failed_backend="wot",
        failure_type="state_conflict",
    )

    assert decision.should_trigger
    assert decision.payload is not None
    assert decision.payload.failed_skill_id == "set_temperature"
    assert suggest_system2_decision(decision.payload)["decision"] == "active_perception"


def test_demo_artifacts_are_written(tmp_path):
    paths = write_demo_artifacts(tmp_path)

    assert paths["normal"].exists()
    assert paths["recovery"].exists()
    assert paths["metrics"].exists()
    recovery_trace = json.loads(paths["recovery"].read_text())
    metrics = json.loads(paths["metrics"].read_text())
    assert any(event["event_type"] == "recovery_triggered" for event in recovery_trace)
    assert metrics["values"]["TSR"] == 1.0


def test_runtime_backend_router_prefers_wot_for_device_skills_and_reroutes_visual():
    from src.contracts.types import SkillCall
    from src.runtime.backend_router import RecoveryRoutingContext, RuntimeBackendRouter

    cmap = CognitiveMap(task_id="task_1")
    cmap.add_affordance(
        RuntimeAffordance(
            id="wot_set_temperature",
            source="wot",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="invoke",
            confidence=0.95,
            grounding={},
            skill_names=["set_temperature"],
        )
    )
    cmap.add_affordance(
        RuntimeAffordance(
            id="visual_set_temperature",
            source="visual",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="click",
            confidence=0.9,
            grounding={"mark_id": "M001"},
            skill_names=["set_temperature"],
        )
    )

    router = RuntimeBackendRouter()
    normal = router.select_backend(SkillCall("set_temperature", {"target": 22}), cmap)
    rerouted = router.select_backend(
        SkillCall("set_temperature", {"target": 22}),
        cmap,
        RecoveryRoutingContext(exclude_backends=["wot"]),
    )

    assert normal.backend == "wot"
    assert rerouted.backend == "visual"


def test_recovery_cascade_reroutes_after_non_retryable_backend_failure():
    from src.contracts.types import ExecutionResult, SkillTuple
    from src.recovery.recovery_cascade import RecoveryCascade, RecoveryContext

    skill_tuple = SkillTuple(
        skill_id="confirm_booking",
        description="Confirm booking",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["dom", "visual"],
        preferred_backends=["dom"],
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level="low",
        irreversible=False,
    )
    result = ExecutionResult(
        skill_id="confirm_booking",
        backend_used="dom",
        success=False,
        latency_ms=10,
        confidence=0.0,
        failure_reason="dom_selector_missing",
    )

    action = RecoveryCascade().decide(
        result=result,
        skill_tuple=skill_tuple,
        cognitive_map=CognitiveMap(task_id="task_1"),
        context=RecoveryContext(
            skill_id="confirm_booking",
            failed_backend="dom",
            failure_type="dom_selector_missing",
            tried_backends=["dom"],
        ),
        available_backends=["dom", "visual"],
    )

    assert action.action_type == "reroute"
    assert action.backend == "visual"
    assert action.recovery_tier == 2


def test_epistemic_arbiter_uses_multi_source_support_to_avoid_over_halting():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 22, "dom", confidence=1.0, timestamp_ms=10))
    cmap.add_state_assertion(
        StateAssertion("thermostat_A", "temperature", 22, "visual", confidence=1.0, timestamp_ms=11)
    )
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 24, "wot", confidence=1.0, timestamp_ms=12))
    arbiter = EpistemicArbiter({"temperature": 2.0})

    conflicts = arbiter.check(cmap)

    assert conflicts == []
    assert not arbiter.should_halt_system1(conflicts)


def test_epistemic_arbiter_downweights_stale_observations():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 20, "dom", confidence=1.0, timestamp_ms=1))
    cmap.add_state_assertion(
        StateAssertion("thermostat_A", "temperature", 24, "wot", confidence=1.0, timestamp_ms=20_001)
    )
    arbiter = EpistemicArbiter({"temperature": 2.0}, max_freshness_delta_ms=1000)

    conflicts = arbiter.check(cmap)

    assert conflicts == []


def test_epistemic_arbiter_detects_semantic_consistency_conflict():
    cmap = CognitiveMap(task_id="task_1")
    cmap.add_state_assertion(StateAssertion("readiness", "ready", True, "system", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("projector_A", "power", "off", "wot", timestamp_ms=2))
    arbiter = EpistemicArbiter(
        semantic_rules=[
            SemanticConsistencyRule(
                conflict_type="readiness_projector_inconsistent",
                entity_id="readiness",
                attribute="ready",
                value=True,
                depends_on_entity_id="projector_A",
                depends_on_attribute="power",
                depends_on_value="off",
                severity="high",
            )
        ]
    )

    conflicts = arbiter.check(cmap)

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "readiness_projector_inconsistent"
    assert conflicts[0].severity == "high"
    assert arbiter.should_halt_system1(conflicts)


def test_cognitive_map_rejects_malformed_structured_inputs_and_clamps_confidence():
    cmap = CognitiveMap(task_id="task_bad_inputs")

    with pytest.raises(ValueError):
        cmap.add_state_assertion(StateAssertion("", "temperature", 22, "wot"))
    with pytest.raises(ValueError):
        cmap.add_entity(Entity(id="", type="thermostat"))
    with pytest.raises(ValueError):
        cmap.add_affordance(
            RuntimeAffordance(
                id="bad_affordance",
                source="wot",
                entity_id="",
                action_name="set_temperature",
                action_type="invoke",
                confidence=1.4,
                grounding={},
            )
        )

    cmap.add_state_assertion(StateAssertion("thermostat_A", "temperature", 22, "wot", confidence=1.7))
    assert cmap.get_latest_state("thermostat_A", "temperature").confidence == 1.0


def test_epistemic_arbiter_repeated_checks_update_conflict_instead_of_duplicating():
    cmap = CognitiveMap(task_id="task_repeat")
    cmap.add_state_assertion(StateAssertion("door_A", "lock", "locked", "dom", timestamp_ms=1))
    cmap.add_state_assertion(StateAssertion("door_A", "lock", "unlocked", "wot", timestamp_ms=2))
    arbiter = EpistemicArbiter()

    first = arbiter.check(cmap)
    second = arbiter.check(cmap)

    assert len(first) == 1
    assert len(second) == 1
    assert len(cmap.unresolved_conflicts()) == 1
    assert cmap.unresolved_conflicts()[0].id == "door_A.lock"


def test_epistemic_arbiter_ignores_low_confidence_visual_noise():
    cmap = CognitiveMap(task_id="task_noise")
    cmap.add_state_assertion(StateAssertion("screen_A", "mode", "presenting", "wot", confidence=1.0, timestamp_ms=10))
    cmap.add_state_assertion(StateAssertion("screen_A", "mode", "idle", "visual", confidence=0.2, timestamp_ms=11))
    arbiter = EpistemicArbiter(halt_threshold=1.0)

    conflicts = arbiter.check(cmap)

    assert conflicts == []
    assert cmap.unresolved_conflicts() == []


def test_epistemic_arbiter_halts_on_high_confidence_categorical_conflict():
    cmap = CognitiveMap(task_id="task_categorical")
    cmap.add_state_assertion(StateAssertion("door_A", "lock", "locked", "dom", confidence=1.0, timestamp_ms=10))
    cmap.add_state_assertion(StateAssertion("door_A", "lock", "unlocked", "wot", confidence=1.0, timestamp_ms=11))
    arbiter = EpistemicArbiter(halt_threshold=1.0)

    conflicts = arbiter.check(cmap)

    assert len(conflicts) == 1
    assert conflicts[0].attribute == "lock"
    assert arbiter.should_halt_system1(conflicts)


def test_runtime_backend_router_reports_all_candidates_excluded_and_penalizes_failures():
    from src.contracts.types import SkillCall
    from src.runtime.backend_router import RecoveryRoutingContext, RuntimeBackendRouter

    cmap = CognitiveMap(task_id="task_router_bad_cases")
    cmap.add_affordance(
        RuntimeAffordance(
            id="wot_temperature",
            source="wot",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="invoke",
            confidence=0.95,
            grounding={},
            skill_names=["set_temperature"],
        )
    )
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_temperature",
            source="dom",
            entity_id="thermostat_A",
            action_name="set_temperature",
            action_type="click",
            confidence=0.9,
            grounding={},
            skill_names=["set_temperature"],
        )
    )
    router = RuntimeBackendRouter()

    all_excluded = router.select_backend(
        SkillCall("set_temperature", {"target": 22}),
        cmap,
        RecoveryRoutingContext(exclude_backends=["wot", "dom"]),
    )
    penalized = router.select_backend(
        SkillCall("set_temperature", {"target": 22}),
        cmap,
        RecoveryRoutingContext(exclude_backends=[], previous_failures={"wot": 2}),
    )

    assert all_excluded.backend == ""
    assert all_excluded.reason == "all candidate backends excluded by recovery context"
    assert penalized.backend == "dom"


def test_recovery_cascade_handles_success_and_escalates_unrecoverable_conflict():
    from src.contracts.types import ExecutionResult, SkillTuple
    from src.recovery.recovery_cascade import RecoveryCascade, RecoveryContext
    from src.runtime.cognitive_map import Conflict

    skill_tuple = SkillTuple(
        skill_id="unlock_door",
        description="Unlock meeting room door",
        parameters_schema={},
        preconditions=[],
        postconditions=[],
        allowed_backends=["wot"],
        preferred_backends=["wot"],
        rollback=None,
        failure_modes={},
        timeout_ms=3000,
        safety_level="high",
        irreversible=False,
    )
    success = ExecutionResult("unlock_door", "wot", True, 5, 1.0)
    failed = ExecutionResult("unlock_door", "wot", False, 5, 0.0, failure_reason="unsafe_conflict")
    cmap = CognitiveMap(task_id="task_recovery_bad_cases")
    cmap.add_conflict(
        Conflict(
            id="door_A.lock",
            conflict_type="lock_mismatch",
            sources=["dom", "wot"],
            description="Door lock state conflicts.",
            severity="high",
            conflict_mass=1.0,
        )
    )
    cascade = RecoveryCascade()

    no_recovery = cascade.decide(
        success,
        skill_tuple,
        CognitiveMap(task_id="task_success"),
        RecoveryContext("unlock_door", "wot", ""),
        ["wot"],
    )
    escalation = cascade.decide(
        failed,
        skill_tuple,
        cmap,
        RecoveryContext("unlock_door", "wot", "unsafe_conflict", tried_backends=["wot"]),
        ["wot"],
    )

    assert no_recovery.action_type == "abort"
    assert no_recovery.recovery_tier == 0
    assert escalation.action_type == "escalate_human"
    assert "unresolved perceptual conflict" in escalation.reason


def test_rate_limiter_scopes_entity_operations_and_rejects_empty_keys():
    from src.safety.rate_limiter import RateLimiter

    limiter = RateLimiter(min_interval_s=60.0)

    assert limiter.allow("thermostat_A", "set_temperature")
    assert not limiter.allow("thermostat_A", "set_temperature")
    assert limiter.allow("thermostat_A", "read_temperature")
    assert limiter.remaining_wait_s("thermostat_A", "set_temperature") > 0
    with pytest.raises(ValueError):
        limiter.allow("")
    with pytest.raises(ValueError):
        limiter.allow("thermostat_A", "")


def test_system2_policy_triggers_on_low_confidence_and_unsafe_action():
    policy = System2EscalationPolicy(confidence_threshold=0.85)
    cmap = CognitiveMap(task_id="task_system2_bad_cases")

    low_confidence = policy.decide("confirm_booking", cmap, confidence=0.4)
    unsafe = policy.decide("unlock_door", cmap, unsafe_action=True)
    healthy = policy.decide("verify_readiness", cmap, confidence=0.99)

    assert low_confidence.should_trigger
    assert low_confidence.reason == "low_confidence"
    assert unsafe.should_trigger
    assert unsafe.reason == "unsafe_action_requires_confirmation"
    assert not healthy.should_trigger


def test_cognitive_map_adapts_contract_affordances_for_runtime_routing():
    cmap = CognitiveMap(task_id="task_contract_affordance")
    cmap.update_affordances(
        [
            Affordance(
                id="dom_book_room",
                source="DOM",
                type="button",
                label="Book Room",
                action="click",
                locator={"selector": "#book", "entity_id": "booking_button", "skill_id": "confirm_booking"},
                confidence=0.92,
            )
        ]
    )

    affordance = cmap.get_affordances_for_skill("confirm_booking")[0]

    assert affordance.source == "dom"
    assert affordance.entity_id == "booking_button"
    assert affordance.action_name == "confirm_booking"
    assert affordance.grounding["selector"] == "#book"


def test_cognitive_map_rejects_invalid_contract_affordance_without_partial_update():
    cmap = CognitiveMap(task_id="task_contract_affordance_bad_case")
    existing = Affordance(
        id="dom_existing",
        source="DOM",
        type="button",
        label="Book Room",
        action="click",
        locator={"selector": "#book", "entity_id": "booking_button", "skill_id": "confirm_booking"},
        confidence=0.92,
    )
    cmap.update_affordances([existing])

    with pytest.raises(ValueError):
        cmap.update_affordances(
            [
                existing,
                Affordance(
                    id="",
                    source="DOM",
                    type="button",
                    label="Broken",
                    action="click",
                    locator={"selector": "#broken"},
                    confidence=0.5,
                ),
            ]
        )

    assert list(cmap.runtime_affordances) == ["dom_existing"]
    assert cmap.affordances == [existing]


def test_continuous_interaction_manager_blocks_system1_on_sensory_conflict():
    wot_executor = _RecordingExecutor("wot")
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill_tuple()},
        {"wot": wot_executor},
        CognitiveMap(task_id="task_conflict_gate"),
    )

    result = asyncio.run(
        manager.run_skill(
            SkillCall("set_temperature", {"target": 22}),
            Observation(
                device_states={"thermostat_A": {"temperature": 24}},
                accessibility_tree={"page_state": {"thermostat_A": {"temperature": 20}}},
            ),
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.recovery_tier == 4
    assert "sensory conflict detected" in result.reason
    assert result.conflict_ids == ["thermostat_A.temperature"]
    assert result.failure_boundary == "recoverable_execution_failure"
    assert result.failure_type == "sensory_conflict"
    assert result.fusion_decision["allow_system1"] is False
    assert result.fusion_decision["active_perception_required"] is True
    assert result.fusion_decision["fused_states"][0]["entity_id"] == "thermostat_A"
    assert wot_executor.calls == []


def test_continuous_interaction_manager_uses_active_perception_to_resolve_conflict_before_execution():
    wot_executor = _RecordingExecutor("wot")
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill_tuple()},
        {"wot": wot_executor},
        CognitiveMap(task_id="task_conflict_resolved"),
        active_perception_resolver=ActivePerceptionResolver(_ResolvingProbe()),
    )

    result = asyncio.run(
        manager.run_skill(
            SkillCall("set_temperature", {"target": 22}),
            Observation(
                device_states={"thermostat_A": {"temperature": 24}},
                accessibility_tree={"page_state": {"thermostat_A": {"temperature": 20}}},
            ),
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.active_perception_trace[0]["action"] == "active_perception_probe"
    assert result.active_perception_trace[0]["resolved"] is True
    assert wot_executor.calls
    assert manager.cognitive_map.unresolved_conflicts() == []


def test_epistemic_arbiter_fuses_live_style_dom_wot_visual_majority():
    cmap = CognitiveMap(task_id="task_live_fusion")
    cmap.add_state_assertion(
        StateAssertion("thermostat", "target_temperature", 22, "wot", confidence=1.0, timestamp_ms=10)
    )
    cmap.add_state_assertion(
        StateAssertion("thermostat", "target_temperature", 22, "dom", confidence=0.85, timestamp_ms=11)
    )
    cmap.add_state_assertion(
        StateAssertion("thermostat", "target_temperature", 23, "visual", confidence=0.35, timestamp_ms=12)
    )

    decision = EpistemicArbiter(numeric_tolerances={"target_temperature": 2.0}).fuse(cmap)

    assert decision.allow_system1 is True
    assert decision.active_perception_required is False
    assert decision.conflicts == []
    fused = next(
        state
        for state in decision.fused_states
        if state.entity_id == "thermostat" and state.attribute == "target_temperature"
    )
    assert fused.value == 22
    assert set(fused.sources) == {"wot", "dom"}
    assert fused.confidence > 0.8


def test_epistemic_arbiter_allows_missing_visual_when_primary_sources_agree():
    cmap = CognitiveMap(task_id="task_missing_visual")
    cmap.add_state_assertion(StateAssertion("projector", "power", "on", "wot", confidence=1.0, timestamp_ms=10))
    cmap.add_state_assertion(StateAssertion("projector", "power", "on", "dom", confidence=0.8, timestamp_ms=11))

    decision = EpistemicArbiter().fuse(cmap)

    assert decision.allow_system1 is True
    assert decision.conflicts == []
    assert decision.fused_states[0].value == "on"


def test_epistemic_arbiter_downweights_stale_live_dashboard_state():
    cmap = CognitiveMap(task_id="task_stale_dashboard")
    cmap.add_state_assertion(
        StateAssertion("thermostat", "target_temperature", 24, "dom", confidence=1.0, timestamp_ms=1)
    )
    cmap.add_state_assertion(
        StateAssertion("thermostat", "target_temperature", 22, "wot", confidence=1.0, timestamp_ms=10_001)
    )

    decision = EpistemicArbiter(
        numeric_tolerances={"target_temperature": 1.0},
        max_freshness_delta_ms=1000,
    ).fuse(cmap)

    assert decision.allow_system1 is True
    assert decision.conflicts == []
    fused = next(state for state in decision.fused_states if state.attribute == "target_temperature")
    assert fused.value == 22
    assert fused.sources == ["wot"]


def test_active_perception_result_records_unresolved_conflict():
    result = ActivePerceptionResult(
        resolved=False,
        trace=[{"action": "active_perception_probe", "resolved": False}],
    )

    assert result.resolved is False
    assert result.trace[0]["resolved"] is False


def test_continuous_interaction_manager_uses_runtime_backend_router_when_affordance_matches():
    dom_executor = _RecordingExecutor("dom")
    wot_executor = _RecordingExecutor("wot")
    cmap = CognitiveMap(task_id="task_runtime_router")
    cmap.add_affordance(
        RuntimeAffordance(
            id="dom_confirm_booking",
            source="dom",
            entity_id="booking_button",
            action_name="confirm_booking",
            action_type="button",
            confidence=0.95,
            grounding={"selector": "#book"},
            skill_names=["confirm_booking"],
        )
    )
    manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "wot"],
                preferred_backends=["wot"],
            )
        },
        {"dom": dom_executor, "wot": wot_executor},
        cmap,
    )

    result = asyncio.run(manager.run_skill(SkillCall("confirm_booking", {"room": "A"}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert result.selected_backend == "dom"
    assert result.routing_reason == "selected dom for confirm_booking"
    assert len(dom_executor.calls) == 1
    assert wot_executor.calls == []


def test_continuous_interaction_manager_falls_back_to_preferred_backend_without_runtime_affordance():
    dom_executor = _RecordingExecutor("dom")
    wot_executor = _RecordingExecutor("wot")
    manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "wot"],
                preferred_backends=["wot"],
            )
        },
        {"dom": dom_executor, "wot": wot_executor},
        CognitiveMap(task_id="task_router_fallback"),
    )

    result = asyncio.run(manager.run_skill(SkillCall("confirm_booking", {"room": "A"}), Observation()))

    assert result.state == RuntimeState.COMPLETED
    assert result.selected_backend == "wot"
    assert result.routing_reason == "fallback preferred backend wot"
    assert len(wot_executor.calls) == 1
    assert dom_executor.calls == []


def test_continuous_interaction_manager_handles_unknown_skill_without_crashing():
    manager = ContinuousInteractionManager(
        {"set_temperature": _skill_tuple()},
        {"wot": _RecordingExecutor("wot")},
        CognitiveMap(task_id="task_unknown_skill"),
    )

    result = asyncio.run(manager.run_skill(SkillCall("missing_skill", {}), Observation()))

    assert result.state == RuntimeState.FAILED
    assert result.execution_result is None
    assert result.reason == "unknown skill: missing_skill"
    assert result.failure_boundary == "skill_spec_insufficient"
    assert result.failure_type == "unknown_skill"


def test_continuous_interaction_manager_converts_executor_exception_to_recovery_decision():
    manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "visual"],
                preferred_backends=["dom"],
            )
        },
        {
            "dom": _RecordingExecutor("dom", exception=TimeoutError("playwright timed out")),
            "visual": _RecordingExecutor("visual"),
        },
        CognitiveMap(task_id="task_executor_exception"),
    )

    result = asyncio.run(manager.run_skill(SkillCall("confirm_booking", {}), Observation()))

    assert result.state == RuntimeState.RECOVERING
    assert result.recovery_tier == 2
    assert result.selected_backend == "visual"
    assert result.execution_result is not None
    assert result.execution_result.failure_reason == "executor_exception:TimeoutError"
    assert result.failure_boundary == "immediate_runtime_error"
    assert result.failure_type == "timeout"
    assert [step["policy"] for step in result.recovery_trace[:2]] == ["retry", "reroute"]
    assert result.recovery_trace[1]["selected"] is True


def test_continuous_interaction_manager_uses_recovery_cascade_for_failures_and_postconditions():
    failed_executor = _RecordingExecutor(
        "dom",
        ExecutionResult(
            skill_id="confirm_booking",
            backend_used="dom",
            success=False,
            latency_ms=1.0,
            confidence=0.0,
            failure_reason="selector_not_found",
        ),
    )
    failure_manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "visual"],
                preferred_backends=["dom"],
            )
        },
        {"dom": failed_executor, "visual": _RecordingExecutor("visual")},
        CognitiveMap(task_id="task_failure_recovery"),
    )

    failure = asyncio.run(failure_manager.run_skill(SkillCall("confirm_booking", {}), Observation()))

    assert failure.state == RuntimeState.RECOVERING
    assert failure.recovery_tier == 2
    assert failure.selected_backend == "visual"
    assert failure.recovery_trace[1]["policy"] == "reroute"

    postcondition_manager = ContinuousInteractionManager(
        {
            "set_temperature": _skill_tuple(
                postconditions=[Condition("thermostat_A.targetTemperature == 22")],
                rollback=RollbackSpec("set_temperature", {"target": 20}),
            )
        },
        {"wot": _RecordingExecutor("wot")},
        CognitiveMap(task_id="task_postcondition_recovery"),
    )

    postcondition = asyncio.run(
        postcondition_manager.run_skill(SkillCall("set_temperature", {"target": 22}), Observation())
    )

    assert postcondition.state == RuntimeState.RECOVERING
    assert postcondition.recovery_tier == 3
    assert postcondition.reason == "rollback spec available"


def test_continuous_interaction_manager_can_attach_optional_llm_failure_judgment():
    failed_executor = _RecordingExecutor(
        "dom",
        ExecutionResult(
            skill_id="confirm_booking",
            backend_used="dom",
            success=False,
            latency_ms=1.0,
            confidence=0.0,
            failure_reason="ambiguous_false_success",
        ),
    )
    manager = ContinuousInteractionManager(
        {
            "confirm_booking": _skill_tuple(
                skill_id="confirm_booking",
                allowed_backends=["dom", "visual"],
                preferred_backends=["dom"],
            )
        },
        {"dom": failed_executor, "visual": _RecordingExecutor("visual")},
        CognitiveMap(task_id="task_llm_judge"),
        llm_judge=LLMJudge(client=_FakeJudgeClient()),
        use_llm_judge=True,
    )

    result = asyncio.run(manager.run_skill(SkillCall("confirm_booking", {}), Observation()))

    assert result.state == RuntimeState.RECOVERING
    assert result.failure_boundary == "recoverable_execution_failure"
    assert result.llm_failure_boundary == "skill_spec_insufficient"
    assert result.llm_failure_type == "weak_postcondition"
    assert result.llm_judge_evidence[-1] == "schema_validated_llm_judge"


def test_continuous_interaction_manager_runs_structured_goal_without_durable_skill():
    dom_executor = _RecordingExecutor(
        "dom",
        ExecutionResult(
            skill_id="reserve_room_goal",
            backend_used="dom",
            success=True,
            latency_ms=2.0,
            confidence=1.0,
            raw_observation_delta={"booking": {"confirmed": True}},
        ),
    )
    cmap = CognitiveMap(task_id="task_goal_path")
    cmap.update_affordances(
        [
            Affordance(
                id="dom_room_input",
                source="DOM",
                type="input",
                label="Room",
                action="type",
                locator={"entity_id": "booking_form"},
                confidence=0.95,
            ),
            Affordance(
                id="dom_time_input",
                source="DOM",
                type="input",
                label="Time",
                action="type",
                locator={"entity_id": "booking_form"},
                confidence=0.95,
            ),
            Affordance(
                id="dom_confirm_booking",
                source="DOM",
                type="button",
                label="Confirm booking",
                action="click",
                locator={"entity_id": "booking_button"},
                confidence=0.95,
            ),
        ]
    )
    manager = ContinuousInteractionManager({}, {"dom": dom_executor}, cmap)

    result = asyncio.run(
        manager.run_goal(
            goal_id="reserve_room_goal",
            goal_state="device_states.booking.confirmed == true",
            parameters={"room": "A", "time": "14:00"},
            observation=Observation(),
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.reason == "goal completed"
    assert result.selected_backend == "dom"
    assert [step["action"] for step in result.primitive_plan] == ["type", "type", "click"]
    assert [call.params["affordance_id"] for call in dom_executor.calls] == [
        "dom_room_input",
        "dom_time_input",
        "dom_confirm_booking",
    ]


def test_continuous_interaction_manager_observes_live_page_before_zero_shot_goal():
    dom_executor = _RecordingExecutor(
        "dom",
        ExecutionResult(
            skill_id="reserve_room_goal",
            backend_used="dom",
            success=True,
            latency_ms=2.0,
            confidence=1.0,
            raw_observation_delta={"booking": {"confirmed": True}},
        ),
    )
    page = PageAffordanceModel(
        page_id="booking_page",
        url="https://example.test/booking",
        affordances=[
            Affordance(
                id="dom_room_input",
                source="DOM",
                type="input",
                label="Room",
                action="type",
                locator={"entity_id": "booking_form"},
                confidence=0.95,
            ),
            Affordance(
                id="dom_time_input",
                source="DOM",
                type="input",
                label="Time",
                action="type",
                locator={"entity_id": "booking_form"},
                confidence=0.95,
            ),
            Affordance(
                id="dom_confirm_booking",
                source="DOM",
                type="button",
                label="Confirm booking",
                action="click",
                locator={"entity_id": "booking"},
                confidence=0.95,
            ),
        ],
        raw_node_count=100,
        kept_node_count=3,
    )
    live_observation = observation_from_live_sources(
        page=page,
        device_states={"booking": {"confirmed": False}},
        page_state={"booking": {"confirmed": False}},
    )
    cmap = CognitiveMap(task_id="task_observe_first_goal")
    manager = ContinuousInteractionManager({}, {"dom": dom_executor}, cmap)

    result = asyncio.run(
        manager.run_observed_goal(
            live_observation,
            goal_id="reserve_room_goal",
            goal_state="device_states.booking.confirmed == true",
            parameters={"room": "A", "time": "14:00"},
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert result.reason == "goal completed"
    assert "dom_room_input" in cmap.runtime_affordances
    assert cmap.page_state["page"]["url"] == "https://example.test/booking"
    assert [step["action"] for step in result.primitive_plan] == ["type", "type", "click"]
    assert result.fusion_decision["allow_system1"] is True


def test_live_observation_fusion_reports_multisource_support_without_halting():
    page = PageAffordanceModel(
        page_id="dashboard",
        url="https://example.test",
        affordances=[],
    )
    live_observation = observation_from_live_sources(
        page=page,
        device_states={"booking": {"confirmed": True}},
        page_state={"booking": {"confirmed": True}},
        visual_state={"booking": {"confirmed": True}},
    )
    cmap = CognitiveMap(task_id="task_live_multisource_fusion")
    live_observation.apply_to(cmap)

    decision = EpistemicArbiter().fuse(cmap)
    booking = next(
        state for state in decision.fused_states if state.entity_id == "booking" and state.attribute == "confirmed"
    )

    assert decision.allow_system1 is True
    assert decision.active_perception_required is False
    assert set(booking.sources) == {"wot", "dom", "visual"}
    assert booking.confidence == 1.0


def test_continuous_interaction_manager_accepts_goal_spec_boundary():
    dom_executor = _RecordingExecutor(
        "dom",
        ExecutionResult(
            skill_id="reserve_room_goal",
            backend_used="dom",
            success=True,
            latency_ms=1.0,
            confidence=1.0,
            raw_observation_delta={"booking": {"confirmed": True}},
        ),
    )
    cmap = CognitiveMap(task_id="task_goal_spec")
    cmap.update_affordances(
        [
            Affordance(
                id="dom_room_input",
                source="DOM",
                type="input",
                label="Room",
                action="type",
                locator={"entity_id": "booking_form"},
                confidence=0.95,
            ),
            Affordance(
                id="dom_confirm_booking",
                source="DOM",
                type="button",
                label="Confirm booking",
                action="click",
                locator={"entity_id": "booking_button"},
                confidence=0.95,
            ),
        ]
    )
    manager = ContinuousInteractionManager({}, {"dom": dom_executor}, cmap)

    result = asyncio.run(
        manager.run_goal(
            observation=Observation(),
            goal_spec=GoalSpec(
                goal_id="reserve_room_goal",
                goal_state="device_states.booking.confirmed == true",
                parameters={"room": "A"},
                source="user_intent_parser",
                safety_constraints=["only use declared affordances"],
            ),
        )
    )

    assert result.state == RuntimeState.COMPLETED
    assert dom_executor.calls[0].skill_id == "reserve_room_goal"
    assert result.primitive_plan[-1]["action"] == "click"


def test_continuous_interaction_manager_rejects_invalid_goal_spec():
    manager = ContinuousInteractionManager(
        {}, {"dom": _RecordingExecutor("dom")}, CognitiveMap(task_id="bad_goal_spec")
    )

    result = asyncio.run(
        manager.run_goal(
            observation=Observation(),
            goal_spec=GoalSpec(goal_id="", goal_state="", parameters={}),
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.failure_type == "invalid_goal_spec"
    assert "goal_id must be non-empty" in result.plan_validation_errors


def test_continuous_interaction_manager_rejects_goal_plan_with_missing_affordance():
    manager = ContinuousInteractionManager(
        {}, {"dom": _RecordingExecutor("dom")}, CognitiveMap(task_id="task_bad_goal")
    )

    result = asyncio.run(
        manager.run_goal(
            goal_id="reserve_room_goal",
            goal_state="device_states.booking.confirmed == true",
            parameters={"room": "A"},
            observation=Observation(),
        )
    )

    assert result.state == RuntimeState.ESCALATED
    assert result.failure_boundary == "skill_spec_insufficient"
    assert result.failure_type == "insufficient_affordance_plan"
    assert "room" in result.reason
    assert result.primitive_plan[0]["action"] == "ask_user"
