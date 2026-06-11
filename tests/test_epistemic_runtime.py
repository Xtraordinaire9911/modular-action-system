"""Tests for structured CognitiveMap, epistemic arbitration, and demo traces."""

import json

import pytest

from evaluation.integration_eval import write_demo_artifacts
from src.recovery.system2_escalation import System2EscalationPolicy, suggest_system2_decision
from src.runtime.cognitive_map import CognitiveMap, Entity, RuntimeAffordance, StateAssertion
from src.verification.conflict_detector import EpistemicArbiter, SemanticConsistencyRule, SensoryConflictError


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
