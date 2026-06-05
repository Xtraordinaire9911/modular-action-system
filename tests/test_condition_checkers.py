"""Runtime control tests for empirical pre/postcondition checking."""

from src.contracts.types import Condition, ExecutionResult, Observation, SkillCall
from src.runtime.cognitive_map import CognitiveMap
from src.verification.postcondition_checker import PostconditionChecker
from src.verification.precondition_checker import PreconditionChecker


def test_cognitive_map_merges_observation_and_execution_delta():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.update_from_observation(
        Observation(device_states={"thermostat_A": {"online": True}})
    )
    cognitive_map.record_execution_result(
        ExecutionResult(
            skill_id="set_temperature",
            backend_used="wot",
            success=True,
            latency_ms=12,
            confidence=1.0,
            raw_observation_delta={"thermostat_A": {"targetTemperature": 22}},
        )
    )

    assert cognitive_map.device_states["thermostat_A"]["online"] is True
    assert cognitive_map.device_states["thermostat_A"]["targetTemperature"] == 22


def test_precondition_checker_passes_simple_predicates():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.device_states = {"thermostat_A": {"online": True}}
    checker = PreconditionChecker()

    assert checker.passes(
        [Condition("device_states.thermostat_A.online == true")],
        cognitive_map,
    )


def test_postcondition_checker_reports_failed_observed_value():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.device_states = {"thermostat_A": {"targetTemperature": 20}}
    checker = PostconditionChecker()

    results = checker.check(
        [Condition("device_states.thermostat_A.targetTemperature == 22")],
        cognitive_map,
    )

    assert not results[0].passed
    assert results[0].observed == 20
    assert results[0].expected == 22


def test_cognitive_map_tracks_current_skill_and_conflicts():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.set_current_skill(SkillCall("confirm_booking", {"room": "A"}))
    conflict = cognitive_map.mark_conflict("occupancy", ["page", "device"], "room state mismatch")

    assert cognitive_map.current_skill is not None
    assert conflict in cognitive_map.unresolved_conflicts()


def test_precondition_checker_supports_compound_param_predicates():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.set_current_skill(SkillCall("set_temperature", {"target": 22}))
    checker = PreconditionChecker()

    results = checker.check(
        [Condition("target >= 16 and target <= 30")],
        cognitive_map,
    )

    assert results[0].passed
    assert results[0].observed == [22, 22]
    assert results[0].expected == [16, 30]


def test_postcondition_checker_resolves_rhs_params_reference():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.set_current_skill(SkillCall("set_temperature", {"target": 22}))
    cognitive_map.device_states = {"thermostat": {"target_temperature": 22}}
    checker = PostconditionChecker()

    results = checker.check(
        [Condition("thermostat.target_temperature == params.target")],
        cognitive_map,
    )

    assert results[0].passed
    assert results[0].observed == 22
    assert results[0].expected == 22


def test_compound_param_predicate_fails_when_out_of_range():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.set_current_skill(SkillCall("set_temperature", {"target": 35}))
    checker = PreconditionChecker()

    results = checker.check(
        [Condition("target >= 16 and target <= 30")],
        cognitive_map,
    )

    assert not results[0].passed
    assert results[0].observed == [35, 35]
    assert results[0].expected == [16, 30]
