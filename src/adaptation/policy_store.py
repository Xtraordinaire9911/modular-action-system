"""Closed-loop policy overlay: turn approved low-risk proposals into behaviour.

The adaptation pipeline deliberately stops at *reviewable* proposals. This
module adds the final, bounded step that closes the loop: a proposal that
passed the release gate (approved + safe_to_apply) and whose change type is in
``LOW_RISK_CHANGE_TYPES`` is written into a persistent policy overlay that the
runtime backend router loads on the next episode — so the agent's behaviour
actually evolves from its own experience. Every application is:

  * gated       — only approved, safe, low-risk proposals are applied;
  * bounded     — magnitudes are capped and only nudge routing preference; skill
                  semantics, postconditions, safety thresholds, and code are
                  never touched (those change types are forbidden upstream);
  * reversible  — each applied change is logged and can be reverted by id;
  * observable  — the overlay serialises to JSON with its applied-change log.

The router consumes the overlay purely by duck typing (``skill_backend_penalty``
and ``preferred_backend``), so ``src.runtime`` keeps no import dependency here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.adaptation.release_gate import FORBIDDEN_CHANGE_TYPES, LOW_RISK_CHANGE_TYPES

# Bounded routing nudge applied per accepted reliability proposal, and its ceiling.
RELIABILITY_PENALTY_STEP = 0.2
PENALTY_CAP = 0.6
_ROUTING_RELIABILITY_TYPES = {"backend_reliability_adjustment", "failure_profile_weight"}


@dataclass
class AppliedChange:
    proposal_id: str
    change_type: str
    skill_id: str
    backend: str
    magnitude: float


@dataclass
class PolicyOverlay:
    """Learned routing adjustments derived only from approved proposals."""

    applied: list[AppliedChange] = field(default_factory=list)
    preferred_overrides: dict[str, str] = field(default_factory=dict)

    def skill_backend_penalty(self, skill_id: str, backend: str) -> float:
        return sum(
            change.magnitude
            for change in self.applied
            if change.change_type in _ROUTING_RELIABILITY_TYPES
            and change.skill_id == skill_id
            and change.backend == backend
        )

    def preferred_backend(self, skill_id: str) -> str | None:
        return self.preferred_overrides.get(skill_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": [asdict(change) for change in self.applied],
            "preferred_overrides": dict(self.preferred_overrides),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyOverlay":
        applied = [AppliedChange(**entry) for entry in data.get("applied", [])]
        overrides = dict(data.get("preferred_overrides", {}))
        return cls(applied=applied, preferred_overrides=overrides)


@dataclass(frozen=True)
class ApplyOutcome:
    proposal_id: str
    applied: bool
    reason: str


def _refuse(proposal_id: str, reason: str) -> ApplyOutcome:
    return ApplyOutcome(proposal_id=proposal_id, applied=False, reason=reason)


def apply_proposal(overlay: PolicyOverlay, proposal: dict[str, Any]) -> ApplyOutcome:
    """Apply one gated proposal to the overlay, or refuse with a reason."""
    proposal_id = str(proposal.get("proposal_id", ""))
    change_type = str(proposal.get("change_type", ""))
    gate = proposal.get("release_gate") or {}

    if change_type in FORBIDDEN_CHANGE_TYPES:
        return _refuse(proposal_id, f"forbidden change type: {change_type}")
    if change_type not in LOW_RISK_CHANGE_TYPES:
        return _refuse(proposal_id, f"unsupported change type: {change_type}")
    if not gate.get("approved", False):
        return _refuse(proposal_id, "release gate not approved")
    if not gate.get("safe_to_apply", False):
        return _refuse(proposal_id, "release gate did not mark the proposal safe_to_apply")
    if any(change.proposal_id == proposal_id for change in overlay.applied):
        return _refuse(proposal_id, "proposal already applied")

    signature = str(proposal.get("signature", ""))
    parts = signature.split("|")
    skill_id = parts[0] if parts and parts[0] else ""
    backend = parts[1] if len(parts) > 1 and parts[1] else ""

    if change_type in _ROUTING_RELIABILITY_TYPES:
        if not skill_id or not backend:
            return _refuse(proposal_id, "proposal signature missing skill_id|backend")
        current = overlay.skill_backend_penalty(skill_id, backend)
        if current >= PENALTY_CAP:
            return _refuse(proposal_id, f"routing penalty for {skill_id}|{backend} already at cap")
        magnitude = min(RELIABILITY_PENALTY_STEP, PENALTY_CAP - current)
        overlay.applied.append(AppliedChange(proposal_id, change_type, skill_id, backend, magnitude))
        return ApplyOutcome(proposal_id, True, f"lowered {skill_id}|{backend} routing preference by {magnitude:.2f}")

    if change_type == "preferred_backend_order_change":
        new_pref = str(proposal.get("new_preferred_backend", ""))
        if not skill_id or not new_pref:
            return _refuse(proposal_id, "preferred_backend_order_change needs skill_id and new_preferred_backend")
        overlay.preferred_overrides[skill_id] = new_pref
        overlay.applied.append(AppliedChange(proposal_id, change_type, skill_id, new_pref, 0.0))
        return ApplyOutcome(proposal_id, True, f"set preferred backend for {skill_id} to {new_pref}")

    # LOW_RISK but not consumed by the router (e.g. new_regression_fixture, retry_budget_adjustment).
    return _refuse(proposal_id, f"{change_type} is not consumed by the router overlay")


def revert_proposal(overlay: PolicyOverlay, proposal_id: str) -> bool:
    """Undo every change an earlier proposal made. Returns True if anything changed."""
    removed = [change for change in overlay.applied if change.proposal_id == proposal_id]
    if not removed:
        return False
    for change in removed:
        if change.change_type == "preferred_backend_order_change":
            overlay.preferred_overrides.pop(change.skill_id, None)
    overlay.applied = [change for change in overlay.applied if change.proposal_id != proposal_id]
    return True


class PolicyStore:
    """Persist the learned overlay so the *next* runtime episode loads it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> PolicyOverlay:
        if not self.path.exists():
            return PolicyOverlay()
        return PolicyOverlay.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, overlay: PolicyOverlay) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(overlay.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return self.path
