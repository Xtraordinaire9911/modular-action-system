"""Compile raw runtime traces into bounded experience records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.adaptation.credit_assignment import CreditAssignment, assign_credit
from src.adaptation.failure_boundary import FailureAnalysis


class RuntimeTraceLike(Protocol):
    task_id: str
    skill_id: str
    backend: str
    status: str
    latency_ms: float
    attempt: int
    recovery_tier: int | None
    failure_reason: str | None
    postcondition_passed: bool | None
    details: dict[str, Any] | None
    episode_id: str
    transition_id: str
    state_id_before: str
    state_id_after: str
    affordance_key: str


@dataclass(frozen=True)
class CompiledExperience:
    experience_id: str
    task_id: str
    skill_id: str
    selected_backend: str
    failure_type: str
    failure_boundary: str
    credit_assignment: CreditAssignment
    evidence: list[str] = field(default_factory=list)
    immediate_action: str = ""
    long_term_candidate: str = ""
    recovery_trace: list[dict[str, Any]] = field(default_factory=list)
    trace_event: dict[str, Any] = field(default_factory=dict)
    validated: bool = False
    safe_to_auto_apply: bool = False
    needs_human_review: bool = True
    episode_id: str = ""
    transition_id: str = ""
    state_id_before: str = ""
    state_id_after: str = ""
    affordance_key: str = ""


class ExperienceCompiler:
    def compile_failure(
        self,
        event: RuntimeTraceLike,
        analysis: FailureAnalysis,
        *,
        recovery_trace: list[dict[str, Any]] | None = None,
        unresolved_conflicts: list[str] | None = None,
    ) -> CompiledExperience:
        credit = assign_credit(
            analysis,
            backend=event.backend,
            unresolved_conflicts=unresolved_conflicts,
        )
        evidence = list(analysis.evidence)
        if event.failure_reason:
            evidence.append(f"failure_reason={event.failure_reason!r}")
        if event.postcondition_passed is not None:
            evidence.append(f"postcondition_passed={event.postcondition_passed}")
        if unresolved_conflicts:
            evidence.extend(unresolved_conflicts)

        return CompiledExperience(
            experience_id=_experience_id(event, analysis),
            task_id=event.task_id,
            skill_id=event.skill_id,
            selected_backend=event.backend,
            failure_type=analysis.failure_type,
            failure_boundary=analysis.boundary.value,
            credit_assignment=credit,
            evidence=evidence,
            immediate_action=analysis.immediate_action,
            long_term_candidate=analysis.long_term_action,
            recovery_trace=recovery_trace or [],
            trace_event=_trace_event_dict(event),
            validated=False,
            safe_to_auto_apply=False,
            needs_human_review=analysis.needs_human_review,
            episode_id=getattr(event, "episode_id", ""),
            transition_id=getattr(event, "transition_id", ""),
            state_id_before=getattr(event, "state_id_before", ""),
            state_id_after=getattr(event, "state_id_after", ""),
            affordance_key=getattr(event, "affordance_key", ""),
        )


def _experience_id(event: RuntimeTraceLike, analysis: FailureAnalysis) -> str:
    parts = [
        "exp",
        event.task_id,
        event.skill_id,
        event.backend or "no_backend",
        analysis.failure_type,
        str(event.attempt),
    ]
    return "_".join(_sanitize(part) for part in parts)


def _sanitize(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_")


def _trace_event_dict(event: RuntimeTraceLike) -> dict[str, Any]:
    return {
        "task_id": event.task_id,
        "skill_id": event.skill_id,
        "backend": event.backend,
        "status": event.status,
        "latency_ms": event.latency_ms,
        "attempt": event.attempt,
        "recovery_tier": event.recovery_tier,
        "failure_reason": event.failure_reason,
        "postcondition_passed": event.postcondition_passed,
        "details": event.details,
        "episode_id": getattr(event, "episode_id", ""),
        "transition_id": getattr(event, "transition_id", ""),
        "state_id_before": getattr(event, "state_id_before", ""),
        "state_id_after": getattr(event, "state_id_after", ""),
        "affordance_key": getattr(event, "affordance_key", ""),
    }
