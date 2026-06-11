"""Structured trace logging for full-system recovery evaluation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class RuntimeTraceEvent:
    task_id: str
    skill_id: str
    backend: str
    status: str
    latency_ms: float = 0.0
    attempt: int = 1
    recovery_tier: int | None = None
    failure_reason: str | None = None
    postcondition_passed: bool | None = None
    details: dict[str, Any] | None = None


class TraceLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.events: list[RuntimeTraceEvent] = []

    def record(self, event: RuntimeTraceEvent) -> None:
        self.events.append(event)

    def write_jsonl(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(asdict(event), sort_keys=True) for event in self.events]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def as_table_rows(self) -> list[dict[str, Any]]:
        return [asdict(event) for event in self.events]
