"""Record the quantities the metrics are computed from, step by step.

The campaign printed TSR, RTR, RSR, RTA and DA at the end and nothing before
it. A reader had to take the numbers on faith: there was no way to see which
episodes contributed to which figure, or to recompute one by hand.

This keeps the intermediate counts as they accumulate - observations made,
elements measured, candidates considered, actions taken, verifications passed
and failed, diagnoses reached, recoveries applied, escalations - so every metric
can be traced back to the tallies it came from.

Displayed in the demo as one quiet line rather than a feature of the layout:
these are working numbers, and they should read as smaller than the step being
narrated, not compete with it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Counters:
    """Raw tallies. Every metric below is a ratio of two of these."""

    observations: int = 0
    elements_seen: int = 0
    elements_measured: int = 0
    candidates_scored: int = 0
    actions: int = 0
    verifications: int = 0
    verify_passed: int = 0
    verify_failed: int = 0
    probes: int = 0
    diagnoses: int = 0
    recoveries: int = 0
    escalations: int = 0
    goals_met: int = 0
    episodes: int = 0

    def as_strip(self) -> str:
        """A single compact line, ordered as the loop runs."""
        return (
            f"obs {self.observations} · seen {self.elements_seen} · meas {self.elements_measured} · "
            f"cand {self.candidates_scored} · act {self.actions} · "
            f"ver {self.verify_passed}/{self.verifications} · probe {self.probes} · "
            f"diag {self.diagnoses} · rec {self.recoveries} · esc {self.escalations}"
        )


@dataclass
class MetricLedger:
    """Counters plus the arithmetic that turns them into the reported metrics."""

    counters: Counters = field(default_factory=Counters)
    notes: list[str] = field(default_factory=list)

    # --- recording ---------------------------------------------------------
    def observed(self, elements: int) -> None:
        self.counters.observations += 1
        self.counters.elements_seen += elements

    def measured(self, boxes: int) -> None:
        self.counters.elements_measured += boxes

    def scored(self, candidates: int) -> None:
        self.counters.candidates_scored += candidates

    def acted(self) -> None:
        self.counters.actions += 1

    def verified(self, passed: bool) -> None:
        self.counters.verifications += 1
        if passed:
            self.counters.verify_passed += 1
        else:
            self.counters.verify_failed += 1

    def probed(self, count: int = 1) -> None:
        self.counters.probes += count

    def diagnosed(self, cause: str, tier: int) -> None:
        self.counters.diagnoses += 1
        self.notes.append(f"diagnosis {self.counters.diagnoses}: {cause} -> tier {tier}")

    def recovered(self) -> None:
        self.counters.recoveries += 1

    def escalated(self) -> None:
        self.counters.escalations += 1

    def episode_done(self, goal_met: bool) -> None:
        self.counters.episodes += 1
        if goal_met:
            self.counters.goals_met += 1

    # --- the arithmetic, kept explicit -------------------------------------
    def derivations(self) -> list[tuple[str, str, float]]:
        """Each metric as (name, the division performed, value).

        Written out rather than summarised so a reader can check a figure
        against the tallies instead of trusting it.
        """
        c = self.counters
        rows = [
            ("TSR", f"goals met {c.goals_met} / episodes {c.episodes}", _rate(c.goals_met, c.episodes)),
            ("RTR", f"failures detected {c.verify_failed} / episodes {c.episodes}", _rate(c.verify_failed, c.episodes)),
            (
                "RSR",
                f"recoveries applied {c.recoveries} / failures detected {c.verify_failed}",
                _rate(c.recoveries, c.verify_failed),
            ),
            (
                "verify pass rate",
                f"passed {c.verify_passed} / verifications {c.verifications}",
                _rate(c.verify_passed, c.verifications),
            ),
            (
                "measurement coverage",
                f"measured {c.elements_measured} / seen {c.elements_seen}",
                _rate(c.elements_measured, c.elements_seen),
            ),
        ]
        return rows

    def report(self) -> str:
        lines = ["intermediate quantities", "", f"  {self.counters.as_strip()}", "", "derived from them:"]
        for name, working, value in self.derivations():
            lines.append(f"  {name:<22} {working:<44} = {value:6.1%}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counters": asdict(self.counters),
            "derivations": [{"metric": n, "working": w, "value": round(v, 4)} for n, w, v in self.derivations()],
            "notes": list(self.notes),
        }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = ["Counters", "MetricLedger"]
