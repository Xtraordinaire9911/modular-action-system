"""Episode budgets, fresh-observation boundary, and verified transitions."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from src.contracts.types import ExecutionResult, Observation
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.live_observation import LiveRuntimeObservation


@dataclass(frozen=True)
class EpisodePolicy:
    max_steps: int = 20
    deadline_s: float = 60.0
    max_retry_attempts: int = 2
    max_attempts_per_backend: int = 4
    require_fresh_observation: bool = False

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.deadline_s <= 0:
            raise ValueError("deadline_s must be positive")
        if self.max_retry_attempts < 0:
            raise ValueError("max_retry_attempts cannot be negative")
        if self.max_attempts_per_backend <= 0:
            raise ValueError("max_attempts_per_backend must be positive")


@dataclass(frozen=True)
class ObservationRequest:
    task_id: str
    episode_id: str
    reason: str
    step: int
    previous_result: ExecutionResult | None = None


class ObservationProvider(Protocol):
    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation | Observation: ...


@dataclass
class CancellationToken:
    cancelled: bool = False
    reason: str = ""

    def cancel(self, reason: str = "human cancellation") -> None:
        self.cancelled = True
        self.reason = reason


@dataclass
class EpisodeContext:
    task_id: str
    policy: EpisodePolicy
    episode_id: str = field(default_factory=lambda: f"episode-{uuid.uuid4().hex[:12]}")
    started_monotonic: float = field(default_factory=time.monotonic)
    step_count: int = 0
    retry_count: int = 0
    backend_attempts: dict[str, int] = field(default_factory=dict)
    tried_backends: list[str] = field(default_factory=list)
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    paused_started_monotonic: float | None = None
    paused_duration_s: float = 0.0

    def terminal_reason(self) -> str | None:
        if self.cancellation.cancelled:
            return self.cancellation.reason or "episode cancelled"
        if self.step_count >= self.policy.max_steps:
            return "episode max_steps exhausted"
        if self.elapsed_s() >= self.policy.deadline_s:
            return "episode deadline exceeded"
        return None

    def pause_clock(self) -> None:
        if self.paused_started_monotonic is None:
            self.paused_started_monotonic = time.monotonic()

    def resume_clock(self) -> None:
        if self.paused_started_monotonic is not None:
            self.paused_duration_s += time.monotonic() - self.paused_started_monotonic
            self.paused_started_monotonic = None

    def elapsed_s(self) -> float:
        paused = self.paused_duration_s
        if self.paused_started_monotonic is not None:
            paused += time.monotonic() - self.paused_started_monotonic
        return max(0.0, time.monotonic() - self.started_monotonic - paused)

    def can_attempt_backend(self, backend: str) -> bool:
        return self.backend_attempts.get(backend, 0) < self.policy.max_attempts_per_backend

    def begin_attempt(self, backend: str) -> tuple[int, str]:
        reason = self.terminal_reason()
        if reason:
            raise RuntimeError(reason)
        if not self.can_attempt_backend(backend):
            raise RuntimeError(f"backend attempt budget exhausted: {backend}")
        self.step_count += 1
        self.backend_attempts[backend] = self.backend_attempts.get(backend, 0) + 1
        if backend not in self.tried_backends:
            self.tried_backends.append(backend)
        return self.backend_attempts[backend], f"{self.episode_id}:transition-{self.step_count:04d}"


@dataclass(frozen=True)
class TransitionRecord:
    task_id: str
    episode_id: str
    transition_id: str
    step: int
    state_id_before: str
    state_id_after: str
    skill_id: str
    affordance_key: str
    backend: str
    params: dict[str, object]
    success: bool
    execution_success: bool
    postcondition_passed: bool | None
    latency_ms: float
    attempt: int
    observation_delta: dict[str, object]
    recovery_action: str = ""
    recovery_tier: int | None = None
    recovery_of_transition_id: str = ""
    failure_reason: str = ""
    reversible_result: bool | None = None
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class TransitionLedger:
    """Append-only in-memory ledger with optional JSONL persistence."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.records: list[TransitionRecord] = []

    def record(self, transition: TransitionRecord) -> None:
        self.records.append(transition)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(transition), sort_keys=True) + "\n")

    def for_episode(self, episode_id: str) -> list[TransitionRecord]:
        return [record for record in self.records if record.episode_id == episode_id]


def abstract_state_id(cognitive_map: CognitiveMap) -> str:
    """Fingerprint latest source facts and stable affordance identities."""

    latest: dict[tuple[str, str, str], object] = {}
    timestamps: dict[tuple[str, str, str], int] = {}
    for assertion in cognitive_map.state_assertions:
        key = (assertion.source, assertion.entity_id, assertion.attribute)
        if key not in timestamps or assertion.timestamp_ms >= timestamps[key]:
            latest[key] = assertion.value
            timestamps[key] = assertion.timestamp_ms
    facts = [
        {
            "source": key[0],
            "entity": _normalize_dynamic_token(key[1]),
            "attribute": key[2],
            "value": _normalize_state_value(key[2], value),
        }
        for key, value in sorted(latest.items())
    ]
    affordances = sorted(
        _stable_affordance_key(affordance.id, affordance.source, affordance.entity_id, affordance.grounding)
        for affordance in cognitive_map.runtime_affordances.values()
        if not _is_demo_overlay(affordance.grounding)
    )
    payload = json.dumps({"facts": facts, "affordances": affordances}, sort_keys=True, default=str)
    return f"state-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def stable_affordance_key(cognitive_map: CognitiveMap, affordance_id: str) -> str:
    affordance = cognitive_map.runtime_affordances.get(affordance_id)
    if affordance is None:
        return affordance_id
    return _stable_affordance_key(
        affordance.id,
        affordance.source,
        affordance.entity_id,
        affordance.grounding,
    )


def _stable_affordance_key(
    affordance_id: str,
    source: str,
    entity_id: str,
    grounding: dict[str, object],
) -> str:
    if grounding.get("stable_key"):
        identity = str(grounding["stable_key"])
    elif grounding.get("thing_id"):
        identity = f"thing:{grounding['thing_id']}"
    elif grounding.get("href"):
        identity = f"href:{_normalize_url(str(grounding['href']))}"
    elif grounding.get("mark_id"):
        identity = f"mark:{grounding['mark_id']}"
    elif grounding.get("role") or grounding.get("label"):
        identity = f"semantic:{grounding.get('role', '')}:{grounding.get('label', '')}"
    elif grounding.get("selector"):
        identity = f"selector:{_normalize_selector(str(grounding['selector']))}"
    else:
        identity = _normalize_dynamic_token(entity_id or affordance_id)
    return f"{source}:{identity}"


def _is_demo_overlay(grounding: dict[str, object]) -> bool:
    return bool(grounding.get("demo_overlay") or grounding.get("runtime_overlay"))


def _normalize_state_value(attribute: str, value: object) -> object:
    if attribute in {"url", "href"} and isinstance(value, str):
        return _normalize_url(value)
    if isinstance(value, str):
        return _normalize_dynamic_token(value)
    return value


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value)
    path = "/".join(_normalize_dynamic_token(part) for part in parsed.path.split("/"))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _normalize_selector(value: str) -> str:
    value = re.sub(r":nth-(?:child|of-type)\(\d+\)", ":nth-child({index})", value)
    value = re.sub(r"([#._-])[0-9a-f]{8,}(?=\b|[-_])", r"\1{id}", value, flags=re.IGNORECASE)
    return value


def _normalize_dynamic_token(value: str) -> str:
    value = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b",
        "{uuid}",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"(?<![A-Za-z])\d{4,}(?![A-Za-z])", "{id}", value)
