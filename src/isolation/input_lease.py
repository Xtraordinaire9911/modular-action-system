"""Cooperative input ownership for a supervised execution session.

The lease gives agent and operator adapters one shared software check before
they send an action.  It does not capture or block a physical keyboard or
mouse at operating-system level; a separate desktop or remote-input boundary
is required for that stronger guarantee.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncContextManager, AsyncIterator, Generic, ParamSpec, Protocol, TypeVar

_ExecuteArgs = ParamSpec("_ExecuteArgs")
_ExecuteResult = TypeVar("_ExecuteResult", covariant=True)


class InputOwner(str, Enum):
    """The actor currently allowed to send software-controlled input."""

    AGENT = "agent"
    HUMAN = "human"
    NONE = "none"


class InputLeaseDenied(PermissionError):
    """Raised when an actor tries to act without owning the input lease."""


@dataclass
class InputLease:
    """A cooperative gate that owns every guarded action until it finishes."""

    # Fail closed until a provider has finished provisioning the session.
    owner: InputOwner = InputOwner.NONE
    _active_actions: int = field(default=0, init=False, repr=False, compare=False)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition, init=False, repr=False, compare=False)
    _transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False, compare=False)

    @property
    def active_actions(self) -> int:
        """Number of guarded software actions that have not returned yet."""

        return self._active_actions

    def transfer_to(self, owner: InputOwner) -> None:
        """Immediately transfer an idle lease.

        Runtime lifecycle code should use :meth:`transfer_when_idle`, which
        safely waits for an already-running action.  This synchronous method is
        retained for setup code and fails closed if an action is in flight.
        """

        self._validate_owner(owner)
        if self._active_actions:
            raise RuntimeError("cannot transfer the input lease while a guarded action is running")
        self.owner = owner

    async def transfer_when_idle(self, owner: InputOwner) -> None:
        """Block new actions, await in-flight work, then transfer ownership."""

        self._validate_owner(owner)
        async with self._transition_lock:
            async with self._condition:
                # Changing to NONE while waiting is important: a second agent
                # action must not sneak in after pause/cleanup has started.
                self.owner = InputOwner.NONE
                await self._condition.wait_for(lambda: self._active_actions == 0)
                self.owner = owner

    def revoke(self) -> None:
        """Immediately revoke an idle lease; fail if an action is in flight."""

        self.transfer_to(InputOwner.NONE)

    async def revoke_when_idle(self) -> None:
        """Block new actions and wait until all guarded actions have returned."""

        await self.transfer_when_idle(InputOwner.NONE)

    def allows(self, actor: InputOwner) -> bool:
        """Return whether ``actor`` currently owns the lease."""

        if not isinstance(actor, InputOwner):
            raise TypeError("input actor must be an InputOwner")
        return actor is not InputOwner.NONE and self.owner is actor

    def require(self, actor: InputOwner) -> None:
        """Raise unless ``actor`` currently owns the lease.

        Agent executors and human-control adapters should call this immediately
        before each software action they send.
        """

        if not self.allows(actor):
            raise InputLeaseDenied(f"{actor.value} input is denied; current input owner is {self.owner.value}")

    @asynccontextmanager
    async def input_action(self, actor: InputOwner) -> AsyncIterator[None]:
        """Hold the actor's lease for one complete asynchronous action."""

        async with self._condition:
            self.require(actor)
            self._active_actions += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active_actions -= 1
                self._condition.notify_all()

    @staticmethod
    def _validate_owner(owner: InputOwner) -> None:
        if not isinstance(owner, InputOwner):
            raise TypeError("input owner must be an InputOwner")


class InputGuard(Protocol):
    """The small part of an isolation session needed by guarded adapters."""

    def require_input(self, actor: InputOwner) -> None: ...


class AsyncInputGuard(InputGuard, Protocol):
    """A guard that reserves ownership for a complete asynchronous action."""

    def input_action(self, actor: InputOwner) -> AsyncContextManager[None]: ...


class AsyncExecutor(Protocol[_ExecuteArgs, _ExecuteResult]):
    """Any asynchronous executor with an ``execute`` method."""

    async def execute(self, *_args: _ExecuteArgs.args, **_kwargs: _ExecuteArgs.kwargs) -> _ExecuteResult: ...


class AgentInputGuardedExecutor(Generic[_ExecuteArgs, _ExecuteResult]):
    """Hold the agent lease for the complete delegated asynchronous action.

    This adapter can wrap DOM, visual, WoT, or test executors without knowing
    their argument or result types.  It makes the cooperative lease enforceable
    for that software path while leaving the wrapped executor unchanged.
    """

    def __init__(
        self,
        session: InputGuard,
        executor: AsyncExecutor[_ExecuteArgs, _ExecuteResult],
    ) -> None:
        self.session = session
        self.executor = executor

    async def execute(self, *args: _ExecuteArgs.args, **kwargs: _ExecuteArgs.kwargs) -> _ExecuteResult:
        action_guard = getattr(self.session, "input_action", None)
        if not callable(action_guard):
            # Compatibility for third-party providers that expose only the old
            # synchronous assertion. BrowserWotIsolationProvider implements the
            # stronger whole-call reservation used by the shared runtime.
            self.session.require_input(InputOwner.AGENT)
            return await self.executor.execute(*args, **kwargs)
        async with action_guard(InputOwner.AGENT):
            return await self.executor.execute(*args, **kwargs)
