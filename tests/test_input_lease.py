"""Focused tests for cooperative agent/operator input ownership."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from src.isolation import AgentInputGuardedExecutor, BrowserWotIsolationProvider, InputLeaseDenied, InputOwner
from src.runtime.episode import EpisodeContext, EpisodePolicy


class _Browser:
    async def recreate(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _Control:
    def __init__(self) -> None:
        self.lease_id = ""
        self.checkpoint: dict[str, Any] = {"state": {"lights": {"on": False}}}

    async def acquire_lease(self, episode_id: str) -> dict[str, Any]:
        self.lease_id = f"lease:{episode_id}"
        return {
            "lease_id": self.lease_id,
            "checkpoint": copy.deepcopy(self.checkpoint),
        }

    async def restore_lease(self) -> dict[str, Any]:
        return copy.deepcopy(self.checkpoint)

    async def release_lease(self) -> dict[str, Any]:
        self.lease_id = ""
        return copy.deepcopy(self.checkpoint)


class _RecordingExecutor:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def execute(self, action: str) -> str:
        self.actions.append(action)
        return f"executed:{action}"


class _BlockingExecutor(_RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, action: str) -> str:
        self.actions.append(action)
        self.started.set()
        await self.release.wait()
        return f"executed:{action}"


def test_provider_transfers_and_revokes_the_software_input_lease() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("lease-test", EpisodePolicy()))

        # The agent starts with control; a human-control adapter is blocked.
        session.require_input(InputOwner.AGENT)
        assert session.input_owner == "agent"
        with pytest.raises(InputLeaseDenied, match="human input is denied"):
            session.require_input(InputOwner.HUMAN)

        # Pause transfers control to the human adapter and blocks agent actions.
        await provider.pause(session)
        session.require_input(InputOwner.HUMAN)
        assert session.input_owner == "human"
        with pytest.raises(InputLeaseDenied, match="agent input is denied"):
            session.require_input(InputOwner.AGENT)

        # Resume gives control back to the agent and blocks human actions again.
        await provider.resume(session)
        session.require_input(InputOwner.AGENT)
        assert session.input_owner == "agent"
        with pytest.raises(InputLeaseDenied, match="human input is denied"):
            session.require_input(InputOwner.HUMAN)

        # A disposed session grants input to nobody.
        await provider.dispose(session)
        assert session.input_owner == "none"
        with pytest.raises(InputLeaseDenied, match="current input owner is none"):
            session.require_input(InputOwner.AGENT)
        with pytest.raises(InputLeaseDenied, match="current input owner is none"):
            session.require_input(InputOwner.HUMAN)

    asyncio.run(scenario())


def test_restoring_a_session_also_revokes_input() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("restore-test", EpisodePolicy()))

        await provider.restore(session)

        assert session.input_owner == "none"
        with pytest.raises(InputLeaseDenied):
            session.require_input(InputOwner.AGENT)
        await provider.dispose(session)

    asyncio.run(scenario())


def test_input_lease_requires_typed_owners() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("typed-test", EpisodePolicy()))

        with pytest.raises(TypeError, match="InputOwner"):
            session.input_lease.transfer_to("human")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="InputOwner"):
            session.input_lease.require("agent")  # type: ignore[arg-type]
        await provider.dispose(session)

    asyncio.run(scenario())


def test_guarded_agent_executor_blocks_without_the_agent_lease_and_resumes() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("executor-test", EpisodePolicy()))
        delegate = _RecordingExecutor()
        executor = AgentInputGuardedExecutor(session, delegate)

        assert await executor.execute("first") == "executed:first"

        await provider.pause(session)
        with pytest.raises(InputLeaseDenied, match="current input owner is human"):
            await executor.execute("blocked-during-takeover")
        assert delegate.actions == ["first"]

        await provider.resume(session)
        assert await executor.execute("after-resume") == "executed:after-resume"

        await provider.dispose(session)
        with pytest.raises(InputLeaseDenied, match="current input owner is none"):
            await executor.execute("blocked-after-disposal")
        assert delegate.actions == ["first", "after-resume"]

    asyncio.run(scenario())


def test_provider_can_guard_executors_built_before_provision() -> None:
    provider = BrowserWotIsolationProvider(_Browser(), _Control())

    with pytest.raises(InputLeaseDenied, match="no isolation session is active"):
        provider.require_input(InputOwner.AGENT)

    async def scenario() -> None:
        session = await provider.provision(EpisodeContext("dynamic-guard", EpisodePolicy()))
        try:
            provider.require_input(InputOwner.AGENT)
            await provider.pause(session)
            with pytest.raises(InputLeaseDenied, match="current input owner is human"):
                provider.require_input(InputOwner.AGENT)
        finally:
            await provider.dispose(session)

    asyncio.run(scenario())

    with pytest.raises(InputLeaseDenied, match="no isolation session is active"):
        provider.require_input(InputOwner.AGENT)


def test_pause_blocks_new_actions_and_waits_for_the_in_flight_action() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("pause-waits", EpisodePolicy()))
        delegate = _BlockingExecutor()
        executor = AgentInputGuardedExecutor(provider, delegate)

        running_action = asyncio.create_task(executor.execute("already-started"))
        await delegate.started.wait()
        pause = asyncio.create_task(provider.pause(session))
        await asyncio.sleep(0)

        assert not pause.done()
        assert session.input_owner == "none"
        with pytest.raises(InputLeaseDenied, match="current input owner is none"):
            await executor.execute("must-not-start")
        assert delegate.actions == ["already-started"]

        delegate.release.set()
        assert await running_action == "executed:already-started"
        await pause
        assert session.input_owner == "human"
        await provider.dispose(session)

    asyncio.run(scenario())


def test_dispose_revokes_new_actions_and_waits_for_the_in_flight_action() -> None:
    async def scenario() -> None:
        provider = BrowserWotIsolationProvider(_Browser(), _Control())
        session = await provider.provision(EpisodeContext("dispose-waits", EpisodePolicy()))
        delegate = _BlockingExecutor()
        executor = AgentInputGuardedExecutor(provider, delegate)

        running_action = asyncio.create_task(executor.execute("already-started"))
        await delegate.started.wait()
        dispose = asyncio.create_task(provider.dispose(session))
        await asyncio.sleep(0)

        assert not dispose.done()
        assert session.input_owner == "none"
        with pytest.raises(InputLeaseDenied, match="current input owner is none"):
            await executor.execute("must-not-start")

        delegate.release.set()
        await running_action
        await dispose
        assert session.disposed
        assert delegate.actions == ["already-started"]

    asyncio.run(scenario())
