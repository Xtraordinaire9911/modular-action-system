"""Tier-4 supervised takeover: pause, hand over, resume, and measure it.

``HumanEscalationPolicy`` decides *whether* a run should escalate, but the run
never actually stopped for anyone: escalation was a label attached to a result,
so there was no handover to observe and nothing to report about it.

This turns that decision into a real boundary. An episode is paused with a
reason, a human resumes it, and the resume records whether they actually changed
anything. Keeping "looked and did nothing" distinct from "applied a correction"
is what makes the resulting rate meaningful — counting pauses alone would
overstate how often a human was needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


def _default_clock() -> int:
    """Monotonic milliseconds: immune to wall-clock jumps during a long pause."""
    return time.monotonic_ns() // 1_000_000


@dataclass
class TakeoverRecord:
    episode_id: str
    reason: str
    paused_at_ms: int
    resumed_at_ms: int | None = None
    corrected: bool = False
    correction: str = ""

    @property
    def is_open(self) -> bool:
        return self.resumed_at_ms is None

    @property
    def wait_ms(self) -> int | None:
        """How long the run was actually blocked; None while still paused."""
        if self.resumed_at_ms is None:
            return None
        return self.resumed_at_ms - self.paused_at_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "reason": self.reason,
            "paused_at_ms": self.paused_at_ms,
            "resumed_at_ms": self.resumed_at_ms,
            "wait_ms": self.wait_ms,
            "corrected": self.corrected,
            "correction": self.correction,
        }


class TakeoverStateError(RuntimeError):
    """Raised when pause/resume are called out of order."""


@dataclass
class SupervisedTakeover:
    """Records tier-4 handovers so they can be counted, not just claimed."""

    clock: Callable[[], int] = _default_clock
    records: list[TakeoverRecord] = field(default_factory=list)
    _open: TakeoverRecord | None = None

    def pause(self, episode_id: str, reason: str) -> TakeoverRecord:
        if self._open is not None:
            # A second pause would silently discard the first handover's timing.
            raise TakeoverStateError(f"episode {self._open.episode_id!r} is already paused")
        record = TakeoverRecord(episode_id=episode_id, reason=reason, paused_at_ms=self.clock())
        self.records.append(record)
        self._open = record
        return record

    def resume(self, correction: str = "") -> TakeoverRecord:
        """Resume the paused episode. A non-empty correction marks it corrected."""
        if self._open is None:
            raise TakeoverStateError("resume() called while no episode is paused")
        record = self._open
        record.resumed_at_ms = self.clock()
        record.correction = correction
        record.corrected = bool(correction)
        self._open = None
        return record

    @property
    def is_paused(self) -> bool:
        return self._open is not None

    def metrics(self) -> dict[str, Any]:
        """Measurable HITL correction, derived only from completed handovers."""
        closed = [r for r in self.records if not r.is_open]
        corrected = [r for r in closed if r.corrected]
        waits = [r.wait_ms for r in closed if r.wait_ms is not None]
        return {
            "pauses": len(self.records),
            "completed": len(closed),
            "corrections": len(corrected),
            # Share of handovers where a human actually changed something.
            "correction_rate": (len(corrected) / len(closed)) if closed else 0.0,
            "mean_wait_ms": (sum(waits) / len(waits)) if waits else 0.0,
            "total_wait_ms": sum(waits),
        }
