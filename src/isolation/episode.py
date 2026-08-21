"""Small, honest isolation boundary for supervised browser/WoT sessions.

This provider is not the Windows RDP desktop described by UFO2. It gives the
current web/WoT runtime one clean browser context and one
reversible smart-room checkpoint per episode.  Episodes are serialized because
the demo WoT server has a single physical state; a later provider can replace
this class with a Windows child session or a per-episode container without
changing the runtime contract.
"""

from __future__ import annotations

import asyncio
import copy
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Protocol

from src.isolation.input_lease import InputLease, InputLeaseDenied, InputOwner
from src.runtime.episode import EpisodeContext


class IsolationState(str, Enum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    PAUSED = "paused"
    RESTORED = "restored"
    DISPOSED = "disposed"


class BrowserIsolationSurface(Protocol):
    async def recreate(self) -> None: ...

    async def stop(self) -> None: ...


class WotCheckpointSurface(Protocol):
    @property
    def lease_id(self) -> str: ...

    async def acquire_lease(self, episode_id: str) -> dict[str, Any] | None: ...

    async def restore_lease(self) -> dict[str, Any]: ...

    async def release_lease(self) -> dict[str, Any]: ...


@dataclass
class EpisodeIsolationSession:
    task_id: str
    episode_id: str
    checkpoint: dict[str, Any]
    lease_id: str = ""
    state: IsolationState = IsolationState.PROVISIONING
    input_lease: InputLease = field(default_factory=InputLease)
    provisioned_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    restored: bool = False
    disposed: bool = False

    @property
    def input_owner(self) -> str:
        """Backward-compatible text view of the typed software input lease."""

        return self.input_lease.owner.value

    def require_input(self, actor: InputOwner) -> None:
        """Assert that ``actor`` may send a software-controlled action."""

        self.input_lease.require(actor)

    @asynccontextmanager
    async def input_action(self, actor: InputOwner) -> AsyncIterator[None]:
        """Reserve this session's input ownership for one software action."""

        async with self.input_lease.input_action(actor):
            yield


class EpisodeIsolationProvider(Protocol):
    async def provision(self, episode: EpisodeContext) -> EpisodeIsolationSession: ...

    async def checkpoint(self, session: EpisodeIsolationSession) -> dict[str, Any]: ...

    async def pause(self, session: EpisodeIsolationSession) -> None: ...

    async def resume(self, session: EpisodeIsolationSession) -> None: ...

    async def restore(self, session: EpisodeIsolationSession) -> None: ...

    async def dispose(self, session: EpisodeIsolationSession) -> None: ...


class BrowserWotIsolationProvider:
    """Serialize episodes, recreate the browser, and restore exact WoT state."""

    def __init__(
        self,
        browser: BrowserIsolationSurface,
        control: WotCheckpointSurface,
        *,
        lease_wait_timeout_s: float = 30.0,
        lease_retry_s: float = 0.05,
    ) -> None:
        self.browser = browser
        self.control = control
        self.lease_wait_timeout_s = lease_wait_timeout_s
        self.lease_retry_s = lease_retry_s
        self._lock = asyncio.Lock()
        self._active: EpisodeIsolationSession | None = None

    @property
    def active_session(self) -> EpisodeIsolationSession | None:
        return self._active

    def require_input(self, actor: InputOwner) -> None:
        """Gate an action through the currently active session's lease.

        Executors are assembled before provisioning, so they can guard through
        the provider and still fail closed before startup and after cleanup.
        """

        if self._active is None:
            raise InputLeaseDenied(f"{actor.value} input is denied; no isolation session is active")
        self._active.require_input(actor)

    @asynccontextmanager
    async def input_action(self, actor: InputOwner) -> AsyncIterator[None]:
        """Reserve input through whichever episode is currently active."""

        session = self._active
        if session is None:
            raise InputLeaseDenied(f"{actor.value} input is denied; no isolation session is active")
        async with session.input_action(actor):
            yield

    async def provision(self, episode: EpisodeContext) -> EpisodeIsolationSession:
        await self._lock.acquire()
        session: EpisodeIsolationSession | None = None
        lease_acquired = False
        provisioned = False
        try:
            if self._active is not None:
                raise RuntimeError(f"isolation session already active: {self._active.episode_id}")
            lease = await self._acquire_server_lease(episode.episode_id)
            lease_acquired = True
            baseline = copy.deepcopy(lease["checkpoint"])
            session = EpisodeIsolationSession(
                episode.task_id,
                episode.episode_id,
                baseline,
                lease_id=str(lease["lease_id"]),
            )
            self._active = session
            await self.browser.recreate()
            await session.input_lease.transfer_when_idle(InputOwner.AGENT)
            session.state = IsolationState.ACTIVE
            provisioned = True
            return session
        except BaseException as primary_error:
            cleanup_error: BaseException | None = None
            if lease_acquired:
                try:
                    await self.control.release_lease()
                except BaseException as exc:
                    cleanup_error = exc
            if session is not None and self._active is session:
                self._active = None
            if cleanup_error is not None:
                raise primary_error from cleanup_error
            raise
        finally:
            if not provisioned and self._lock.locked():
                self._lock.release()

    async def checkpoint(self, session: EpisodeIsolationSession) -> dict[str, Any]:
        self._require_active(session)
        return copy.deepcopy(session.checkpoint)

    async def pause(self, session: EpisodeIsolationSession) -> None:
        self._require_active(session)
        await session.input_lease.transfer_when_idle(InputOwner.HUMAN)
        session.state = IsolationState.PAUSED

    async def resume(self, session: EpisodeIsolationSession) -> None:
        self._require_active(session)
        await session.input_lease.transfer_when_idle(InputOwner.AGENT)
        session.state = IsolationState.ACTIVE

    async def restore(self, session: EpisodeIsolationSession) -> None:
        self._require_known(session)
        if session.restored:
            return
        await session.input_lease.revoke_when_idle()
        await self.control.restore_lease()
        session.restored = True
        session.state = IsolationState.RESTORED

    async def dispose(self, session: EpisodeIsolationSession) -> None:
        self._require_known(session)
        primary_error: BaseException | None = None
        chained_error: BaseException | None = None
        try:
            # Fail closed before touching browser or room state, and do not race
            # cleanup against an already-started guarded action.
            await session.input_lease.revoke_when_idle()
            if not session.restored:
                try:
                    await self.restore(session)
                except BaseException as exc:
                    primary_error = exc
            try:
                await self.browser.stop()
            except BaseException as exc:
                if primary_error is None:
                    primary_error = exc
                else:
                    chained_error = exc
            try:
                await self.control.release_lease()
                session.restored = True
            except BaseException as exc:
                if primary_error is None:
                    primary_error = exc
                else:
                    chained_error = exc
        finally:
            # revoke_when_idle above normally handled this. A synchronous
            # fallback keeps cleanup fail-closed if a later operation failed.
            if session.input_lease.active_actions == 0:
                session.input_lease.revoke()
            session.state = IsolationState.DISPOSED
            session.disposed = True
            if self._active is session:
                self._active = None
            if self._lock.locked():
                self._lock.release()
        if primary_error is not None:
            if chained_error is not None:
                raise primary_error from chained_error
            raise primary_error

    async def _acquire_server_lease(self, episode_id: str) -> dict[str, Any]:
        if self.control.lease_id:
            await self.control.release_lease()
        deadline = time.monotonic() + self.lease_wait_timeout_s
        while True:
            lease = await self.control.acquire_lease(episode_id)
            if lease is not None:
                return lease
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for WoT episode lease: {episode_id}")
            await asyncio.sleep(self.lease_retry_s)

    def _require_active(self, session: EpisodeIsolationSession) -> None:
        self._require_known(session)
        if session.disposed or session.state in {IsolationState.RESTORED, IsolationState.DISPOSED}:
            raise RuntimeError(f"isolation session is not active: {session.episode_id}")

    def _require_known(self, session: EpisodeIsolationSession) -> None:
        if self._active is not session:
            raise RuntimeError(f"isolation session is not owned by this provider: {session.episode_id}")
