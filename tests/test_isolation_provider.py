"""Focused tests for the browser/WoT episode-isolation boundary."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

import src.runtime.live_environment as live_environment
from src.isolation import BrowserWotIsolationProvider, IsolationState
from src.runtime.episode import EpisodeContext, EpisodePolicy
from src.runtime.live_environment import ThreadedBrowserSession

_CLEAN_ROOM = {
    "state": {
        "thermostat": {"targetTemperature": 20, "schedule": [18, 20]},
        "lights": {"brightness": 100},
    },
    "faults": {},
}


class _FakeBrowser:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.generation = 0

    async def recreate(self) -> None:
        self.generation += 1
        self.events.append(f"browser.recreate:{self.generation}")

    async def stop(self) -> None:
        self.events.append(f"browser.stop:{self.generation}")


class _FakeLeaseBackend:
    def __init__(self, state: dict[str, Any]) -> None:
        self.current = copy.deepcopy(state)
        self.active_lease = ""
        self.checkpoint: dict[str, Any] | None = None
        self.serial = 0

    def acquire(self, episode_id: str) -> dict[str, Any] | None:
        if self.active_lease:
            return None
        self.serial += 1
        self.active_lease = f"lease-{self.serial}"
        self.checkpoint = copy.deepcopy(self.current)
        self.current = copy.deepcopy(_CLEAN_ROOM)
        return {
            "status": "acquired",
            "episode_id": episode_id,
            "lease_id": self.active_lease,
            "checkpoint": copy.deepcopy(self.checkpoint),
        }

    def restore(self, lease_id: str) -> dict[str, Any]:
        if lease_id != self.active_lease or self.checkpoint is None:
            raise RuntimeError("invalid fake lease")
        self.current = copy.deepcopy(self.checkpoint)
        return copy.deepcopy(self.current)

    def release(self, lease_id: str) -> dict[str, Any]:
        restored = self.restore(lease_id)
        self.active_lease = ""
        self.checkpoint = None
        return restored


class _FakeControl:
    def __init__(
        self,
        state: dict[str, Any] | None = None,
        events: list[str] | None = None,
        *,
        backend: _FakeLeaseBackend | None = None,
    ) -> None:
        self.backend = backend or _FakeLeaseBackend(state or _CLEAN_ROOM)
        self.events = events if events is not None else []
        self.restore_payloads: list[dict[str, Any]] = []
        self.lease_id = ""
        self.fail_restore = 0
        self.fail_release_before_restore = 0
        self.fail_release_after_restore = 0

    @property
    def current(self) -> dict[str, Any]:
        return self.backend.current

    @current.setter
    def current(self, value: dict[str, Any]) -> None:
        self.backend.current = value

    async def acquire_lease(self, episode_id: str) -> dict[str, Any] | None:
        self.events.append("control.acquire")
        lease = self.backend.acquire(episode_id)
        if lease is not None:
            self.lease_id = str(lease["lease_id"])
        return lease

    async def restore_lease(self) -> dict[str, Any]:
        self.events.append("control.restore")
        if self.fail_restore:
            self.fail_restore -= 1
            raise RuntimeError("simulated restore failure")
        payload = self.backend.restore(self.lease_id)
        self.restore_payloads.append(payload)
        return payload

    async def release_lease(self) -> dict[str, Any]:
        self.events.append("control.release")
        if self.fail_release_before_restore:
            self.fail_release_before_restore -= 1
            raise RuntimeError("simulated release request failure")
        payload = self.backend.release(self.lease_id)
        self.lease_id = ""
        if self.fail_release_after_restore:
            self.fail_release_after_restore -= 1
            raise RuntimeError("simulated release response failure")
        return payload


def _episode(name: str) -> EpisodeContext:
    return EpisodeContext(name, EpisodePolicy())


def test_provider_resets_state_and_recreates_a_fresh_browser_for_each_episode():
    async def scenario() -> None:
        events: list[str] = []
        baseline = {
            "state": {"thermostat": {"targetTemperature": 23}},
            "faults": {"thermostat": {"type": "timeout", "delay_ms": 50}},
        }
        browser = _FakeBrowser(events)
        control = _FakeControl(baseline, events)
        provider = BrowserWotIsolationProvider(browser, control)

        first = await provider.provision(_episode("first-task"))
        assert first.state == IsolationState.ACTIVE
        assert first.checkpoint == baseline
        assert control.current == _CLEAN_ROOM
        assert browser.generation == 1
        assert events == ["control.acquire", "browser.recreate:1"]
        await provider.dispose(first)

        second = await provider.provision(_episode("second-task"))
        assert second.episode_id != first.episode_id
        assert browser.generation == 2
        assert events[-2:] == ["control.acquire", "browser.recreate:2"]
        await provider.dispose(second)

    asyncio.run(scenario())


@pytest.mark.parametrize("body_raises", [False, True], ids=["success", "exception"])
def test_dispose_restores_the_exact_pre_episode_checkpoint(body_raises: bool):
    async def scenario() -> None:
        baseline = {
            "state": {
                "thermostat": {"targetTemperature": 22, "history": [19, 21, 22]},
                "occupancy": {"occupied": True, "peopleCount": 3},
            },
            "faults": {"lights": {"type": "stale", "nested": {"attempts": [1, 2]}}},
        }
        browser = _FakeBrowser()
        control = _FakeControl(baseline)
        provider = BrowserWotIsolationProvider(browser, control)
        session = await provider.provision(_episode("restore-task"))

        exported_checkpoint = await provider.checkpoint(session)
        exported_checkpoint["state"]["thermostat"]["targetTemperature"] = 99
        assert session.checkpoint == baseline

        try:
            control.current["state"]["thermostat"] = {"targetTemperature": 30}
            control.current["faults"] = {"projector": {"type": "unavailable"}}
            if body_raises:
                raise LookupError("simulated episode failure")
        except LookupError:
            pass
        finally:
            await provider.dispose(session)

        assert control.current == baseline
        assert control.current is not baseline
        assert control.restore_payloads == [baseline]
        assert session.restored
        assert session.disposed
        assert session.state == IsolationState.DISPOSED
        assert session.input_owner == "none"
        assert provider.active_session is None
        assert browser.events[-1] == "browser.stop:1"

    asyncio.run(scenario())


def test_provider_serializes_episodes_and_transfers_the_input_lease():
    async def scenario() -> None:
        browser = _FakeBrowser()
        control = _FakeControl(_CLEAN_ROOM)
        provider = BrowserWotIsolationProvider(browser, control)
        first = await provider.provision(_episode("first"))
        second_waiter = asyncio.create_task(provider.provision(_episode("second")))

        try:
            await asyncio.sleep(0)
            assert not second_waiter.done()
            assert provider.active_session is first

            assert first.input_owner == "agent"
            await provider.pause(first)
            assert first.input_owner == "human"
            assert first.state == IsolationState.PAUSED
            await provider.resume(first)
            assert first.input_owner == "agent"
            assert first.state == IsolationState.ACTIVE

            await provider.dispose(first)
            second = await asyncio.wait_for(second_waiter, timeout=0.5)
            assert provider.active_session is second
            assert second.input_owner == "agent"
            assert browser.generation == 2
            await provider.dispose(second)
        finally:
            if not first.disposed:
                await provider.dispose(first)
            if not second_waiter.done():
                second_waiter.cancel()
                await asyncio.gather(second_waiter, return_exceptions=True)

    asyncio.run(scenario())


def test_two_independent_providers_share_the_server_lease_without_interleaving():
    async def scenario() -> None:
        baseline = {
            "state": {"thermostat": {"targetTemperature": 24}},
            "faults": {"thermostat": {"type": "offline"}},
        }
        backend = _FakeLeaseBackend(baseline)
        first_provider = BrowserWotIsolationProvider(
            _FakeBrowser(),
            _FakeControl(backend=backend),
            lease_wait_timeout_s=0.5,
            lease_retry_s=0.001,
        )
        second_provider = BrowserWotIsolationProvider(
            _FakeBrowser(),
            _FakeControl(backend=backend),
            lease_wait_timeout_s=0.5,
            lease_retry_s=0.001,
        )

        first = await first_provider.provision(_episode("independent-first"))
        backend.current["state"]["thermostat"] = {"targetTemperature": 29}
        second_waiter = asyncio.create_task(second_provider.provision(_episode("independent-second")))
        await asyncio.sleep(0.01)
        assert not second_waiter.done()
        assert backend.current["state"]["thermostat"]["targetTemperature"] == 29

        await first_provider.dispose(first)
        assert backend.current == baseline
        second = await asyncio.wait_for(second_waiter, timeout=0.25)
        assert second.checkpoint == baseline
        assert backend.current == _CLEAN_ROOM
        backend.current["state"]["thermostat"] = {"targetTemperature": 27}
        await second_provider.dispose(second)
        assert backend.current == baseline

    asyncio.run(scenario())


def test_failed_restore_and_release_do_not_deadlock_the_next_local_provision():
    async def scenario() -> None:
        control = _FakeControl(_CLEAN_ROOM)
        provider = BrowserWotIsolationProvider(
            _FakeBrowser(),
            control,
            lease_wait_timeout_s=0.1,
            lease_retry_s=0.001,
        )
        first = await provider.provision(_episode("cleanup-failure"))
        control.fail_restore = 1
        control.fail_release_before_restore = 1

        with pytest.raises(RuntimeError, match="simulated restore failure") as error:
            await provider.dispose(first)
        assert isinstance(error.value.__cause__, RuntimeError)
        assert "release request failure" in str(error.value.__cause__)
        assert provider.active_session is None
        assert control.lease_id
        assert control.backend.active_lease == control.lease_id

        second = await asyncio.wait_for(provider.provision(_episode("after-cleanup-failure")), timeout=0.2)
        assert second.lease_id != first.lease_id
        await provider.dispose(second)

    asyncio.run(scenario())


def test_threaded_browser_recreate_closes_the_old_session_and_launches_a_new_one(monkeypatch):
    class FakeLaunchedSession:
        def __init__(self, serial: int) -> None:
            self.serial = serial
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    launched: list[FakeLaunchedSession] = []
    launch_arguments: list[tuple[str, bool, int]] = []

    def fake_launch(url: str, *, headless: bool, action_timeout_ms: int) -> FakeLaunchedSession:
        launch_arguments.append((url, headless, action_timeout_ms))
        session = FakeLaunchedSession(len(launched) + 1)
        launched.append(session)
        return session

    monkeypatch.setattr(live_environment.BrowserSession, "launch", staticmethod(fake_launch))

    async def scenario() -> None:
        threaded = ThreadedBrowserSession("http://room.test", headless=False, action_timeout_ms=321)
        try:
            await threaded.start()
            await threaded.start()
            assert threaded.context_generation == 1
            assert len(launched) == 1

            await threaded.recreate()
            assert threaded.context_generation == 2
            assert len(launched) == 2
            assert launched[0] is not launched[1]
            assert launched[0].close_calls == 1
        finally:
            await threaded.close()

        assert launched[1].close_calls == 1
        assert launch_arguments == [
            ("http://room.test", False, 321),
            ("http://room.test", False, 321),
        ]

    asyncio.run(scenario())
