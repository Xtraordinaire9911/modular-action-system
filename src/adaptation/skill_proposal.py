"""Mine repeated verified transition chains into review-only skill proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.runtime.episode import TransitionLedger, TransitionRecord

_INTERNAL_PARAMS = {"primitive_action", "affordance_id", "expected_effect"}


@dataclass(frozen=True)
class CandidateSkillStep:
    position: int
    action: str
    affordance_key: str
    backend: str


@dataclass(frozen=True)
class CandidateSkillProposal:
    proposal_id: str
    goal_id: str
    support: int
    source_episode_ids: list[str]
    precondition_state_id: str
    postcondition_state_id: str
    parameters_schema: dict[str, dict[str, str]]
    steps: list[CandidateSkillStep]
    evidence_transition_ids: list[str] = field(default_factory=list)
    safe_to_auto_apply: bool = False
    needs_human_review: bool = True
    status: str = "candidate"


class SkillProposalMiner:
    """Require repeated, fully verified, stable chains before proposing a skill."""

    def __init__(self, *, min_support: int = 3, min_steps: int = 2) -> None:
        if min_support < 2:
            raise ValueError("min_support must be at least 2")
        if min_steps < 2:
            raise ValueError("min_steps must be at least 2")
        self.min_support = min_support
        self.min_steps = min_steps

    def mine(self, ledger: TransitionLedger) -> list[CandidateSkillProposal]:
        episodes = _group_episodes(ledger.records)
        groups: dict[str, list[list[TransitionRecord]]] = {}
        for records in episodes.values():
            if not _eligible_chain(records, self.min_steps):
                continue
            signature = _chain_signature(records)
            groups.setdefault(signature, []).append(records)

        proposals: list[CandidateSkillProposal] = []
        for signature, chains in groups.items():
            distinct_episodes = {chain[0].episode_id for chain in chains}
            if len(distinct_episodes) < self.min_support:
                continue
            first = chains[0]
            proposals.append(
                CandidateSkillProposal(
                    proposal_id=f"skill-{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]}",
                    goal_id=first[0].skill_id,
                    support=len(distinct_episodes),
                    source_episode_ids=sorted(distinct_episodes),
                    precondition_state_id=first[0].state_id_before,
                    postcondition_state_id=first[-1].state_id_after,
                    parameters_schema=_parameter_schema(first),
                    steps=[
                        CandidateSkillStep(
                            position=index,
                            action=str(record.params.get("primitive_action", "invoke")),
                            affordance_key=record.affordance_key,
                            backend=record.backend,
                        )
                        for index, record in enumerate(first, start=1)
                    ],
                    evidence_transition_ids=[record.transition_id for chain in chains for record in chain],
                )
            )
        return proposals


def write_skill_proposals(
    proposals: list[CandidateSkillProposal],
    path: str | Path,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "auto_apply": False,
        "proposals": [asdict(proposal) for proposal in proposals],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _group_episodes(records: list[TransitionRecord]) -> dict[str, list[TransitionRecord]]:
    episodes: dict[str, list[TransitionRecord]] = {}
    for record in records:
        episodes.setdefault(record.episode_id, []).append(record)
    for episode_records in episodes.values():
        episode_records.sort(key=lambda record: record.step)
    return episodes


def _eligible_chain(records: list[TransitionRecord], min_steps: int) -> bool:
    if len(records) < min_steps:
        return False
    if len({record.skill_id for record in records}) != 1:
        return False
    return all(
        record.success
        and record.postcondition_passed is True
        and bool(record.affordance_key)
        and not record.recovery_action
        for record in records
    )


def _chain_signature(records: list[TransitionRecord]) -> str:
    payload = {
        "goal_id": records[0].skill_id,
        "pre": records[0].state_id_before,
        "post": records[-1].state_id_after,
        "steps": [
            {
                "action": record.params.get("primitive_action", "invoke"),
                "affordance_key": record.affordance_key,
                "backend": record.backend,
            }
            for record in records
        ],
        "parameter_types": {
            key: type(value).__name__ for key, value in records[0].params.items() if key not in _INTERNAL_PARAMS
        },
    }
    return json.dumps(payload, sort_keys=True)


def _parameter_schema(records: list[TransitionRecord]) -> dict[str, dict[str, str]]:
    values: dict[str, Any] = {}
    for record in records:
        for key, value in record.params.items():
            if key not in _INTERNAL_PARAMS:
                values.setdefault(key, value)
    return {key: {"type": _json_type(value)} for key, value in sorted(values.items())}


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"
