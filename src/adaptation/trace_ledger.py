"""Episode-level failure ledger for cross-run adaptation evidence."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class EpisodeFailureEvent:
    episode_id: str
    task_id: str
    skill_id: str
    backend: str
    failure_type: str
    boundary: str
    context_key: str
    incident_id: str = ""
    recovery_action: str = ""
    recovery_success: bool = False
    safety_regression: bool = False
    transition_id: str = ""
    state_id_before: str = ""
    state_id_after: str = ""
    affordance_key: str = ""

    @property
    def signature(self) -> str:
        parts = [self.skill_id, self.backend, self.failure_type, self.context_key]
        if self.state_id_before or self.affordance_key:
            parts.extend([self.state_id_before or "unknown_state", self.affordance_key or "unknown_affordance"])
        return "|".join(parts)


class TraceLedger:
    """In-memory ledger used by tests and local adaptation mining."""

    def __init__(self) -> None:
        self.events: list[EpisodeFailureEvent] = []

    def record(self, event: EpisodeFailureEvent) -> None:
        self.events.append(event)

    def group_by_signature(self) -> dict[str, list[EpisodeFailureEvent]]:
        groups: dict[str, list[EpisodeFailureEvent]] = defaultdict(list)
        for event in self.events:
            groups[event.signature].append(event)
        return dict(groups)

    def write_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(asdict(event), sort_keys=True) for event in self.events]
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return target

    @classmethod
    def read_jsonl(cls, path: str | Path) -> TraceLedger:
        ledger = cls()
        source = Path(path)
        if not source.exists():
            return ledger
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ledger.record(EpisodeFailureEvent(**json.loads(line)))
        return ledger
