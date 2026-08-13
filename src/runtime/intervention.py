"""Awaited human-intervention contracts for supervised runtime execution.

The runtime must stop issuing agent actions while a person approves an action or
takes control.  This module provides that synchronization seam without coupling
it to a UI: the runtime awaits :class:`InMemoryInterventionBroker.request`, while
an operator-facing adapter observes the pending request and resolves it.

``RESUME`` is deliberately distinct from ``APPROVE``.  Approval authorizes the
specific pending action.  Resume means that a person may have changed the live
environment, so the caller must obtain a fresh observation and replan before it
allows the agent to act again.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Protocol


class InterventionKind(str, Enum):
    """Why runtime execution has been paused."""

    SAFETY_CONFIRMATION = "safety_confirmation"
    RECOVERY = "recovery"
    CLARIFICATION = "clarification"
    HUMAN_TAKEOVER = "human_takeover"


class InterventionAction(str, Enum):
    """An operator's terminal response to one intervention request."""

    APPROVE = "approve"
    REJECT = "reject"
    RESUME = "resume"
    CANCEL = "cancel"


@dataclass(frozen=True)
class InterventionRequest:
    """One request that must be resolved before agent execution can continue."""

    episode_id: str
    reason: str
    task_id: str = ""
    kind: InterventionKind = InterventionKind.RECOVERY
    intervention_id: str = field(default_factory=lambda: f"intervention-{uuid.uuid4().hex[:12]}")
    requested_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    state_id: str = ""
    pending_action_fingerprint: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, InterventionKind):
            object.__setattr__(self, "kind", InterventionKind(self.kind))
        if not self.episode_id.strip():
            raise ValueError("episode_id must be non-empty")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if not self.intervention_id.strip():
            raise ValueError("intervention_id must be non-empty")
        if self.requested_at_ms < 0:
            raise ValueError("requested_at_ms cannot be negative")


@dataclass(frozen=True)
class InterventionDecision:
    """The operator's answer and any correction metadata they supplied."""

    action: InterventionAction
    actor: str = "human"
    note: str = ""
    correction_applied: bool = False
    decided_at_ms: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.action, InterventionAction):
            object.__setattr__(self, "action", InterventionAction(self.action))
        if not self.actor.strip():
            raise ValueError("actor must be non-empty")
        if self.decided_at_ms < 0:
            raise ValueError("decided_at_ms cannot be negative")

    @property
    def allows_agent_execution(self) -> bool:
        return self.action in {InterventionAction.APPROVE, InterventionAction.RESUME}

    @property
    def requires_replan(self) -> bool:
        """A takeover may have invalidated every pre-intervention grounding."""

        return self.action == InterventionAction.RESUME


@dataclass
class InterventionRecord:
    """Audit record for one resolved or cancelled intervention."""

    episode_id: str
    intervention_id: str
    kind: str
    reason: str
    decision: str
    actor: str
    requested_at_ms: int
    resolved_at_ms: int
    latency_ms: int
    task_id: str = ""
    state_id: str = ""
    pending_action_fingerprint: str = ""
    note: str = ""
    correction_applied: bool = False
    reobserved: bool = False
    replanned: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_resolution(
        cls,
        request: InterventionRequest,
        decision: InterventionDecision,
    ) -> "InterventionRecord":
        resolved_at_ms = decision.decided_at_ms or int(time.time() * 1000)
        return cls(
            episode_id=request.episode_id,
            intervention_id=request.intervention_id,
            kind=request.kind.value,
            reason=request.reason,
            decision=decision.action.value,
            actor=decision.actor,
            requested_at_ms=request.requested_at_ms,
            resolved_at_ms=resolved_at_ms,
            latency_ms=max(0, resolved_at_ms - request.requested_at_ms),
            task_id=request.task_id,
            state_id=request.state_id,
            pending_action_fingerprint=request.pending_action_fingerprint,
            note=decision.note,
            correction_applied=decision.correction_applied,
            metadata={**request.metadata, **decision.metadata},
        )


class InterventionLedger:
    """In-memory intervention audit ledger with optional JSONL persistence.

    Existing JSONL files are loaded before new records are accepted. Blank lines
    are ignored; malformed JSON, invalid record values, and duplicate intervention
    IDs fail initialization with a line-specific :class:`ValueError`. Initialization
    never rewrites a malformed file.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.records = self._load_existing_records()

    def record(self, record: InterventionRecord) -> None:
        validation_error = _intervention_record_validation_error(record)
        if validation_error:
            raise ValueError(f"invalid intervention record: {validation_error}")
        if any(existing.intervention_id == record.intervention_id for existing in self.records):
            raise ValueError(f"duplicate intervention_id: {record.intervention_id}")
        self.records.append(record)
        self._persist()

    def for_episode(self, episode_id: str) -> list[InterventionRecord]:
        return [record for record in self.records if record.episode_id == episode_id]

    def mark_resume_evidence(
        self,
        intervention_id: str,
        *,
        reobserved: bool,
        replanned: bool,
        correction_applied: bool | None = None,
    ) -> InterventionRecord:
        """Attach evidence collected after ``RESUME`` to its audit record."""

        record = next(
            (record for record in self.records if record.intervention_id == intervention_id),
            None,
        )
        if record is None:
            raise KeyError(f"unknown intervention_id: {intervention_id}")
        record.reobserved = reobserved
        record.replanned = replanned
        if correction_applied is not None:
            record.correction_applied = correction_applied
        self._persist()
        return record

    def write_jsonl(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise ValueError("a JSONL path is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(asdict(record), sort_keys=True, default=str) + "\n")
        return destination

    def _persist(self) -> None:
        if self.path is not None:
            self.write_jsonl(self.path)

    def _load_existing_records(self) -> list[InterventionRecord]:
        if self.path is None or not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(f"invalid intervention ledger {self.path}: file is not valid UTF-8") from exc

        records: list[InterventionRecord] = []
        seen_ids: set[str] = set()
        for line_number, raw_line in enumerate(lines, start=1):
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid intervention ledger record at {self.path}:{line_number}: malformed JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError(
                    f"invalid intervention ledger record at {self.path}:{line_number}: expected a JSON object"
                )
            try:
                record = InterventionRecord(**payload)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid intervention ledger record at {self.path}:{line_number}: invalid record shape"
                ) from exc
            validation_error = _intervention_record_validation_error(record)
            if validation_error:
                raise ValueError(f"invalid intervention ledger record at {self.path}:{line_number}: {validation_error}")
            if record.intervention_id in seen_ids:
                raise ValueError(
                    f"invalid intervention ledger record at {self.path}:{line_number}: "
                    f"duplicate intervention_id: {record.intervention_id}"
                )
            seen_ids.add(record.intervention_id)
            records.append(record)
        return records


def _intervention_record_validation_error(record: InterventionRecord) -> str:
    required_text = ("episode_id", "intervention_id", "kind", "reason", "decision", "actor")
    optional_text = ("task_id", "state_id", "pending_action_fingerprint", "note")
    for field_name in (*required_text, *optional_text):
        value = getattr(record, field_name)
        if not isinstance(value, str):
            return f"{field_name} must be a string"
        if field_name in required_text and not value.strip():
            return f"{field_name} must be non-empty"
    if record.kind not in {kind.value for kind in InterventionKind}:
        return f"unknown intervention kind: {record.kind}"
    if record.decision not in {action.value for action in InterventionAction}:
        return f"unknown intervention decision: {record.decision}"
    for field_name in ("requested_at_ms", "resolved_at_ms", "latency_ms"):
        value = getattr(record, field_name)
        if type(value) is not int or value < 0:
            return f"{field_name} must be a non-negative integer"
    for field_name in ("correction_applied", "reobserved", "replanned"):
        if not isinstance(getattr(record, field_name), bool):
            return f"{field_name} must be a boolean"
    if not isinstance(record.metadata, dict):
        return "metadata must be an object"
    return ""


class InterventionBroker(Protocol):
    """Runtime-facing protocol; UI implementations may live outside this module."""

    async def request(self, request: InterventionRequest) -> InterventionDecision: ...


@dataclass
class _PendingIntervention:
    request: InterventionRequest
    future: asyncio.Future[InterventionDecision]


class InMemoryInterventionBroker:
    """Coordinate awaited interventions within one asyncio event loop.

    ``request`` registers the pause and waits.  An operator adapter can call
    ``next_request`` to discover it, then ``resolve`` when the person chooses an
    outcome.  No timeout implicitly approves an action.
    """

    def __init__(
        self,
        ledger: InterventionLedger | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.ledger = ledger or InterventionLedger()
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._pending: dict[str, _PendingIntervention] = {}
        self._seen_ids: set[str] = set()
        self._request_queue: asyncio.Queue[InterventionRequest] = asyncio.Queue()
        self._closed = False

    async def request(self, request: InterventionRequest) -> InterventionDecision:
        if request.intervention_id in self._seen_ids:
            raise ValueError(f"duplicate intervention_id: {request.intervention_id}")
        self._seen_ids.add(request.intervention_id)

        if self._closed:
            decision = InterventionDecision(
                InterventionAction.CANCEL,
                actor="system",
                note="intervention broker is closed",
                decided_at_ms=self._clock_ms(),
            )
            self.ledger.record(InterventionRecord.from_resolution(request, decision))
            return decision

        loop = asyncio.get_running_loop()
        future: asyncio.Future[InterventionDecision] = loop.create_future()
        pending = _PendingIntervention(request, future)
        self._pending[request.intervention_id] = pending
        self._request_queue.put_nowait(request)

        try:
            decision = await future
        except asyncio.CancelledError:
            decision = InterventionDecision(
                InterventionAction.CANCEL,
                actor="system",
                note="intervention waiter was cancelled",
                decided_at_ms=self._clock_ms(),
            )
            self.ledger.record(InterventionRecord.from_resolution(request, decision))
            raise
        else:
            self.ledger.record(InterventionRecord.from_resolution(request, decision))
            return decision
        finally:
            current = self._pending.get(request.intervention_id)
            if current is pending:
                del self._pending[request.intervention_id]

    async def request_intervention(self, request: InterventionRequest) -> InterventionDecision:
        """Explicitly named alias useful for operator-facing adapters."""

        return await self.request(request)

    async def next_request(self, *, timeout_s: float | None = None) -> InterventionRequest:
        """Wait for the next still-pending request."""

        while True:
            if timeout_s is None:
                request = await self._request_queue.get()
            else:
                request = await asyncio.wait_for(self._request_queue.get(), timeout=max(0.0, timeout_s))
            pending = self._pending.get(request.intervention_id)
            if pending is not None and not pending.future.done():
                return request

    def pending_requests(self) -> list[InterventionRequest]:
        return [pending.request for pending in self._pending.values() if not pending.future.done()]

    def resolve(self, intervention_id: str, decision: InterventionDecision) -> None:
        """Resolve a pending request from the same event loop as ``request``."""

        pending = self._pending.get(intervention_id)
        if pending is None:
            raise KeyError(f"unknown intervention_id: {intervention_id}")
        if pending.future.done():
            raise RuntimeError(f"intervention already resolved: {intervention_id}")
        if decision.decided_at_ms == 0:
            decision = replace(decision, decided_at_ms=self._clock_ms())
        pending.future.set_result(decision)

    def close(self, reason: str = "intervention broker closed") -> None:
        """Cancel every pending intervention; future requests are cancelled too."""

        self._closed = True
        for intervention_id, pending in list(self._pending.items()):
            if pending.future.done():
                continue
            self.resolve(
                intervention_id,
                InterventionDecision(
                    InterventionAction.CANCEL,
                    actor="system",
                    note=reason,
                    decided_at_ms=self._clock_ms(),
                ),
            )
