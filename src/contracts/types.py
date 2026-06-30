"""Core shared dataclasses used across all pipeline stages.

All inter-component communication is typed here. Contract violations must be
raised as ContractViolationError and logged to logs/contract_violations.jsonl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


class ContractViolationError(RuntimeError):
    pass


class SensoryConflictError(RuntimeError):
    """Raised when digital and physical observations disagree enough to block System 1."""


# ── Skill-level contracts (owned by Member A, consumed by all) ───────────────


@dataclass
class Condition:
    predicate: str
    description: str = ""


@dataclass
class RollbackSpec:
    skill_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryPolicy:
    tier: int
    action: str
    description: str = ""


@dataclass
class SkillTuple:
    skill_id: str
    description: str
    parameters_schema: dict[str, Any]
    preconditions: list[Condition]
    postconditions: list[Condition]
    allowed_backends: list[str]
    preferred_backends: list[str]
    rollback: RollbackSpec | None
    failure_modes: dict[str, RecoveryPolicy]
    timeout_ms: int
    safety_level: Literal["low", "medium", "high"]
    irreversible: bool


@dataclass
class SkillCall:
    skill_id: str
    params: dict[str, Any]
    priority: int = 0
    required_postconditions: list[Condition] = field(default_factory=list)
    preferred_backends: list[str] = field(default_factory=list)


# ── Affordance (owned by Member B) ───────────────────────────────────────────


@dataclass
class Affordance:
    id: str
    source: Literal["DOM", "VISUAL", "WOT"]
    type: Literal["button", "input", "property", "action", "event", "sensor"]
    label: str
    action: str
    locator: dict[str, Any]
    confidence: float
    state: dict[str, Any] = field(default_factory=dict)
    safety_level: str = "low"


# ── Execution result (owned by Member B, consumed by Member C) ───────────────


@dataclass
class ExecutionResult:
    skill_id: str
    backend_used: str
    success: bool
    latency_ms: float
    confidence: float
    failure_reason: str | None = None
    raw_observation_delta: dict[str, Any] = field(default_factory=dict)


# ── Observation (owned by runtime control) ──────────────────────────────────────────


@dataclass
class Observation:
    screenshot: bytes | None = None
    dom_tree: str | None = None
    accessibility_tree: dict[str, Any] | None = None
    wot_tds: list[dict[str, Any]] | None = None
    device_states: dict[str, Any] = field(default_factory=dict)
    execution_history: list[dict[str, Any]] = field(default_factory=list)


# ── Trace logging ─────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    skill_id: str
    backend: str
    status: str
    latency_ms: float
    attempt: int
    recovery_tier: int | None = None


@dataclass
class EpisodeTrace:
    task_id: str
    entries: list[TraceEntry] = field(default_factory=list)
