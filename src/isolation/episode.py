"""Small, honest isolation boundary for the Project PiP MVP.

This provider does not pretend to be the Windows RDP desktop described by
UFO2.  It gives the current web/WoT runtime one clean browser context and one
reversible smart-room checkpoint per episode.  Episodes are serialized because
the demo WoT server has a single physical state; a later provider can replace
this class with a Windows child session or a per-episode container without
changing the runtime contract.
"""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

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
    input_owner: str = "agent"
    provisioned_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    restored: bool = False
    disposed: bool = False


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
        session.input_owner = "human"
        session.state = IsolationState.PAUSED

    async def resume(self, session: EpisodeIsolationSession) -> None:
        self._require_active(session)
        session.input_owner = "agent"
        session.state = IsolationState.ACTIVE

    async def restore(self, session: EpisodeIsolationSession) -> None:
        self._require_known(session)
        if session.restored:
            return
        await self.control.restore_lease()
        session.restored = True
        session.state = IsolationState.RESTORED

    async def dispose(self, session: EpisodeIsolationSession) -> None:
        self._require_known(session)
        primary_error: BaseException | None = None
        chained_error: BaseException | None = None
        try:
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
            session.input_owner = "none"
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
