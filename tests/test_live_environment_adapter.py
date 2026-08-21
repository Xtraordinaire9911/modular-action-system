import asyncio
import copy

import pytest

import src.runtime.live_environment as live_environment
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.live_environment import (
    AffordanceSemanticBinding,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SkillActionBinding,
    SmartRoomControlClient,
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


class _FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
        return None

    def json(self):
        return self.payload


class _FakeAsyncClient:
    requests = []
    acquire_status = 200
    active_lease = ""
    release_failures_before_server = 0
    release_failures_after_server = 0
    state_payload = {
        "state": {
            "thermostat": {"targetTemperature": 20, "currentTemperature": 19},
            "lights": {"brightness": 100},
            "projector": {"power": "off"},
            "blinds": {"position": 100},
            "occupancy": {"occupied": False, "peopleCount": 0},
        },
        "faults": {},
    }

    def __init__(self, *, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url):
        self.requests.append(("GET", url, None))
        return _FakeResponse(self.state_payload)

    async def post(self, url, *, json, headers=None):
        self.requests.append(("POST", url, copy.deepcopy(json), copy.deepcopy(headers)))
        if url.endswith("/lease/acquire"):
            if type(self).acquire_status == 409 or type(self).active_lease:
                return _FakeResponse({"error": "busy"}, status_code=409)
            type(self).active_lease = "lease-123"
            return _FakeResponse(
                {
                    "status": "acquired",
                    "episode_id": json["episode_id"],
                    "lease_id": "lease-123",
                    "checkpoint": copy.deepcopy(self.state_payload),
                }
            )
        if url.endswith("/lease/release"):
            if not type(self).active_lease:
                return _FakeResponse({"error": "no episode lease is active"}, status_code=409)
            if headers != {"X-Episode-Lease": type(self).active_lease}:
                return _FakeResponse({"error": "control plane is leased"}, status_code=423)
            if type(self).release_failures_before_server:
                type(self).release_failures_before_server -= 1
                raise RuntimeError("simulated release network failure")
            type(self).active_lease = ""
            if type(self).release_failures_after_server:
                type(self).release_failures_after_server -= 1
                raise RuntimeError("simulated lost release response")
            return _FakeResponse({"status": "released", **copy.deepcopy(self.state_payload)})
        if url.endswith("/lease/restore"):
            return _FakeResponse({"status": "restored", **copy.deepcopy(self.state_payload)})
        if url.endswith("/failure"):
            return _FakeResponse({"status": "ok", "faults": {}})
        return _FakeResponse({"status": "restored", **copy.deepcopy(json or {})})


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
                safety_level="high",
            )
        ],
    )
    affordance = Affordance("room", "DOM", "input", "Room", "type", {"selector": "#room"}, 0.9)

    annotated = environment._annotate(affordance)

    assert annotated.locator["binds_parameter"] == "room"
    assert annotated.locator["stable_key"] == "booking.room"
    assert annotated.locator["idempotent"] is True
    assert annotated.safety_level == "high"

    cognitive_map = CognitiveMap(task_id="semantic-risk")
    cognitive_map.update_affordances([annotated])
    assert cognitive_map.runtime_affordances[annotated.id].grounding["safety_level"] == "high"


def test_semantic_binding_without_risk_override_preserves_observed_safety_level():
    environment = SmartRoomLiveEnvironment(  # type: ignore[arg-type]
        _UnusedSession(),
        LiveEnvironmentConfig(),
        semantic_bindings=[AffordanceSemanticBinding("DOM", selector="#book", completion_for="confirm_booking")],
    )
    affordance = Affordance(
        "book",
        "DOM",
        "button",
        "Book Room",
        "click",
        {"selector": "#book"},
        0.9,
        safety_level="medium",
    )

    assert environment._annotate(affordance).safety_level == "medium"


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


def test_control_client_checkpoints_and_restores_complete_state(monkeypatch):
    _FakeAsyncClient.requests = []
    monkeypatch.setattr(live_environment.httpx, "AsyncClient", _FakeAsyncClient)
    client = SmartRoomControlClient("http://control/", timeout_s=1.25)

    checkpoint = asyncio.run(client.checkpoint())
    checkpoint["state"]["thermostat"]["targetTemperature"] = 24

    assert _FakeAsyncClient.state_payload["state"]["thermostat"]["targetTemperature"] == 20
    result = asyncio.run(client.restore(checkpoint))
    checkpoint["state"]["thermostat"]["targetTemperature"] = 26

    assert _FakeAsyncClient.requests[0] == ("GET", "http://control/state", None)
    method, url, posted, headers = _FakeAsyncClient.requests[1]
    assert (method, url) == ("POST", "http://control/restore")
    assert headers is None
    assert posted["state"]["thermostat"]["targetTemperature"] == 24
    assert result["status"] == "restored"


def test_control_client_restore_rejects_non_dictionary_checkpoint():
    client = SmartRoomControlClient("http://control")

    try:
        asyncio.run(client.restore(None))  # type: ignore[arg-type]
    except TypeError as exc:
        assert str(exc) == "checkpoint must be a dictionary"
    else:
        raise AssertionError("restore should reject non-dictionary checkpoints")


def test_control_client_lease_attaches_token_to_mutations_and_clears_it_on_release(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.acquire_status = 200
    _FakeAsyncClient.active_lease = ""
    monkeypatch.setattr(live_environment.httpx, "AsyncClient", _FakeAsyncClient)
    client = SmartRoomControlClient("http://control")

    lease = asyncio.run(client.acquire_lease("episode-client"))
    assert lease is not None
    assert client.lease_id == "lease-123"
    asyncio.run(client.inject("lights", "timeout", delay_ms=5))
    asyncio.run(client.restore_lease())
    asyncio.run(client.release_lease())

    assert _FakeAsyncClient.requests[0] == (
        "POST",
        "http://control/lease/acquire",
        {"episode_id": "episode-client"},
        None,
    )
    for _, _, _, headers in _FakeAsyncClient.requests[1:]:
        assert headers == {"X-Episode-Lease": "lease-123"}
    assert client.lease_id == ""


def test_control_client_reports_busy_lease_without_claiming_a_token(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.acquire_status = 409
    _FakeAsyncClient.active_lease = ""
    monkeypatch.setattr(live_environment.httpx, "AsyncClient", _FakeAsyncClient)
    client = SmartRoomControlClient("http://control")

    assert asyncio.run(client.acquire_lease("episode-busy")) is None
    assert client.lease_id == ""
    _FakeAsyncClient.acquire_status = 200


def test_two_control_clients_cannot_claim_the_same_server_lease(monkeypatch):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.acquire_status = 200
    _FakeAsyncClient.active_lease = ""
    monkeypatch.setattr(live_environment.httpx, "AsyncClient", _FakeAsyncClient)
    first = SmartRoomControlClient("http://control")
    second = SmartRoomControlClient("http://control")

    assert asyncio.run(first.acquire_lease("first")) is not None
    assert asyncio.run(second.acquire_lease("second")) is None
    asyncio.run(first.release_lease())
    assert asyncio.run(second.acquire_lease("second")) is not None
    asyncio.run(second.release_lease())


@pytest.mark.parametrize(
    ("failure_counter", "message", "retry_status"),
    [
        ("release_failures_before_server", "network failure", "released"),
        ("release_failures_after_server", "lost release response", "already_released"),
    ],
)
def test_control_client_retains_release_token_until_retry_is_confirmed(
    monkeypatch, failure_counter: str, message: str, retry_status: str
):
    _FakeAsyncClient.requests = []
    _FakeAsyncClient.acquire_status = 200
    _FakeAsyncClient.active_lease = ""
    _FakeAsyncClient.release_failures_before_server = 0
    _FakeAsyncClient.release_failures_after_server = 0
    setattr(_FakeAsyncClient, failure_counter, 1)
    monkeypatch.setattr(live_environment.httpx, "AsyncClient", _FakeAsyncClient)
    client = SmartRoomControlClient("http://control")

    assert asyncio.run(client.acquire_lease("release-retry")) is not None
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(client.release_lease())
    assert client.lease_id == "lease-123"

    retry = asyncio.run(client.release_lease())
    assert retry["status"] == retry_status
    assert client.lease_id == ""
