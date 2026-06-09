from src.contracts.types import Observation, SkillCall
from src.planner import PlanningGate


def test_planning_gate_allows_consistent_observations():
    observation = Observation(
        device_states={"thermostat": {"targetTemperature": 22}},
        accessibility_tree={"page_state": {"thermostat": {"target_temperature": 22}}},
    )

    result = PlanningGate().evaluate(
        observation=observation,
        skill_call=SkillCall(skill_id="set_temperature", params={"target": 22}),
        task_id="consistent",
    )

    assert result.decision.allow_system1 is True
    assert result.recovery_request is None


def test_planning_gate_blocks_sensory_conflict():
    observation = Observation(
        device_states={"thermostat": {"targetTemperature": 24}},
        accessibility_tree={"page_state": {"thermostat": {"target_temperature": 20}}},
    )

    result = PlanningGate().evaluate(
        observation=observation,
        skill_call=SkillCall(skill_id="set_temperature", params={"target": 22}),
        task_id="conflict",
    )

    assert result.decision.allow_system1 is False
    assert result.decision.conflicts[0].state_key == "thermostat.target_temperature"
    assert result.recovery_request is not None
    assert "System 1 failed or was blocked" in result.recovery_request.prompt
