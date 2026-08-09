"""Snapshot and restore WoT device state between episodes (Member B).

Recreating a browser context isolates the web side, but the devices behind the
Thing Descriptions are shared and persistent: an episode that sets the
thermostat to 26 leaves it there, and the next episode starts from that value.
Nothing reset it, so "isolated episode" only ever described the browser half.

This adds the device half. Before an episode, read every property the TDs
expose; afterwards, write back the ones that changed.

The reporting is deliberately explicit about what it *cannot* undo. A read-only
property, or one that could not be read at snapshot time, is listed rather than
skipped quietly — otherwise a partial rollback reads as a complete one, which is
the more damaging failure when the number is quoted as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.perception.td_affordance_parser import StateAssertionSource


class _WotStateAccess(Protocol):
    def state_sources(self) -> list[StateAssertionSource]: ...
    def read_state(self, source: StateAssertionSource) -> Any: ...
    def write_state(self, source: StateAssertionSource, value: Any) -> None: ...


def _key(source: StateAssertionSource) -> str:
    return f"{source.thing_id}.{source.property}"


@dataclass
class WotSnapshot:
    """What was observed before an episode, and what will not be restorable."""

    values: dict[str, Any] = field(default_factory=dict)
    # Read-only properties: observable, but the episode cannot put them back.
    read_only: list[str] = field(default_factory=list)
    # Properties that could not be read at all, so there is no baseline for them.
    unreadable: list[str] = field(default_factory=list)

    @property
    def restorable(self) -> list[str]:
        return [k for k in self.values if k not in self.read_only]

    @property
    def is_complete(self) -> bool:
        """True only when every exposed property can be rolled back."""
        return not self.read_only and not self.unreadable

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "read_only": list(self.read_only),
            "unreadable": list(self.unreadable),
            "restorable": self.restorable,
            "is_complete": self.is_complete,
        }


@dataclass
class WotRestoreReport:
    restored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "restored": list(self.restored),
            "unchanged": list(self.unchanged),
            "failed": dict(self.failed),
            "skipped": list(self.skipped),
            "ok": self.ok,
        }


def snapshot_state(executor: _WotStateAccess) -> WotSnapshot:
    """Read every exposed property, recording what cannot be rolled back."""
    snapshot = WotSnapshot()
    for source in executor.state_sources():
        key = _key(source)
        try:
            snapshot.values[key] = executor.read_state(source)
        except Exception:
            # No baseline means no rollback for this property; say so.
            snapshot.unreadable.append(key)
            continue
        if source.read_only:
            snapshot.read_only.append(key)
    return snapshot


def restore_state(executor: _WotStateAccess, snapshot: WotSnapshot) -> WotRestoreReport:
    """Write back properties whose value drifted from the snapshot."""
    report = WotRestoreReport()
    for source in executor.state_sources():
        key = _key(source)
        if key not in snapshot.values:
            report.skipped.append(key)  # never had a baseline
            continue
        if source.read_only:
            report.skipped.append(key)
            continue
        expected = snapshot.values[key]
        try:
            current = executor.read_state(source)
        except Exception as exc:
            report.failed[key] = f"re-read failed: {exc}"
            continue
        if current == expected:
            report.unchanged.append(key)  # the episode left it alone
            continue
        try:
            executor.write_state(source, expected)
        except Exception as exc:
            report.failed[key] = f"write failed: {exc}"
            continue
        report.restored.append(key)
    return report


class WotEpisode:
    """Scope a WoT episode so device state is put back when it ends.

    Used as a context manager so an exception inside the episode still restores
    state; leaving devices mutated after a failed run is exactly how one
    episode's failure becomes the next episode's wrong starting point.
    """

    def __init__(self, executor: _WotStateAccess) -> None:
        self._executor = executor
        self.snapshot: WotSnapshot | None = None
        self.report: WotRestoreReport | None = None

    def __enter__(self) -> "WotEpisode":
        self.snapshot = snapshot_state(self._executor)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.snapshot is not None:
            self.report = restore_state(self._executor, self.snapshot)
