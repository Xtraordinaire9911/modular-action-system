import asyncio

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.runtime.live_environment import (
    AffordanceSemanticBinding,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SkillActionBinding,
    SmartRoomLiveEnvironment,
    _prepare_runtime_td,
)


class _UnusedSession:
    pass


class _Effector:
    def __init__(self):
        self.calls = []

    def execute(self, target, observation=None, *, value=None, skill_id=""):
        self.calls.append((target, value, skill_id))
        return ExecutionResult(skill_id, "wot", True, 2.0, 1.0)


def test_runtime_td_uses_stable_title_alias_and_preserves_generated_id():
    td = {
        "id": "urn:uuid:12345678-1234-1234-1234-123456789abc",
        "title": "thermostat",
        "properties": {
            "temperature": {
                "forms": [
                    {
                        "href": "http://172.20.0.2:8080/thermostat/properties/temperature",
                        "op": ["readproperty"],
                    }
                ]
            }
        },
    }

    prepared = _prepare_runtime_td(td, "http://127.0.0.1:18080")

    assert prepared["id"] == "thermostat"
    assert prepared["x-runtime-source-id"] == td["id"]
    assert (
        prepared["properties"]["temperature"]["forms"][0]["href"]
        == "http://127.0.0.1:18080/thermostat/properties/temperature"
    )


def test_semantic_binding_annotates_discovered_affordance_declaratively():
    environment = SmartRoomLiveEnvironment(  # type: ignore[arg-type]
        _UnusedSession(),
        LiveEnvironmentConfig(),
        semantic_bindings=[
            AffordanceSemanticBinding(
                "DOM",
                selector="#room",
                binds_parameter="room",
                stable_key="booking.room",
                idempotent=True,
            )
        ],
    )
    affordance = Affordance("room", "DOM", "input", "Room", "type", {"selector": "#room"}, 0.9)

    annotated = environment._annotate(affordance)

    assert annotated.locator["binds_parameter"] == "room"
    assert annotated.locator["stable_key"] == "booking.room"
    assert annotated.locator["idempotent"] is True


def test_runtime_executor_resolves_durable_skill_to_current_live_affordance():
    environment = SmartRoomLiveEnvironment(  # type: ignore[arg-type]
        _UnusedSession(),
        LiveEnvironmentConfig(),
    )
    affordance = Affordance(
        "set-temp",
        "WOT",
        "action",
        "setTargetTemperature",
        "invoke",
        {"thing_id": "thermostat"},
        1.0,
    )
    environment.latest_affordances = {affordance.id: affordance}
    effector = _Effector()
    executor = RuntimeAffordanceExecutor(
        "wot",
        environment,
        effector,
        skill_bindings=[
            SkillActionBinding(
                "set_temperature",
                "WOT",
                thing_id="thermostat",
                label="setTargetTemperature",
                parameter="target",
            )
        ],
    )

    result = asyncio.run(executor.execute(SkillCall("set_temperature", {"target": 22}), Observation()))

    assert result.success
    assert result.observation_source == "wot"
    assert effector.calls[0][1] == 22
    assert result.metadata["affordance_id"] == "set-temp"
