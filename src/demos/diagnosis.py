"""Work out why a step failed by looking, then choose a recovery accordingly.

Recovery used to be one hardcoded move: re-observe and retry, applied to every
failure regardless of what had happened. It worked for the one fault the demo
injected, which made it look like capability when it was really a rehearsal -
the same fault every time and the same fixed answer.

This diagnoses first. After a failure the agent re-examines the live world and
asks concrete questions: is the target still there, has it moved, has it
vanished, did anything change at all, is there another way to reach the same
goal. The answers - not the scene, and not a lookup keyed by which fault was
injected - decide which recovery tier is used.

The distinction that matters: a diagnosis is only allowed to use what can be
observed after the fact. Nothing here is told which fault was injected, so a
strategy that turns out to be right is evidence the reasoning worked rather
than evidence the demo was arranged.

Tiers follow the project's existing cascade: 1 retry, 2 reroute, 3 rollback,
4 human escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# What the agent concluded. Kept as data rather than branching in place so the
# reasoning can be shown, logged and argued with.
CAUSE_MOVED = "target_moved"
CAUSE_VANISHED = "target_vanished"
CAUSE_INERT = "action_had_no_effect"
CAUSE_OBSCURED = "target_not_actionable"
CAUSE_UNKNOWN = "undiagnosed"

STRATEGY_RETRY = "retry_from_fresh_observation"
STRATEGY_REROUTE = "reroute_to_alternative_affordance"
STRATEGY_ROLLBACK = "rollback_then_retry"
STRATEGY_ESCALATE = "escalate_to_human"


@dataclass
class Diagnosis:
    """What was checked, what it means, and what the agent will do about it."""

    cause: str = CAUSE_UNKNOWN
    strategy: str = STRATEGY_ESCALATE
    tier: int = 4
    evidence: list[str] = field(default_factory=list)
    alternative_label: str = ""
    confidence: float = 0.0

    def explain(self) -> str:
        lines = ["what the agent checked:"]
        lines += [f"  - {item}" for item in self.evidence]
        lines += [
            "",
            f"conclusion: {self.cause}",
            f"strategy:   {self.strategy}  (tier {self.tier})",
            f"confidence: {self.confidence:.2f}",
        ]
        if self.alternative_label:
            lines.append(f"alternative: {self.alternative_label!r}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "strategy": self.strategy,
            "tier": self.tier,
            "evidence": list(self.evidence),
            "alternative": self.alternative_label,
            "confidence": round(self.confidence, 2),
        }


def _label_of(mark: Any) -> str:
    return str(getattr(mark, "label", ""))


def _centre_of(mark: Any) -> tuple[int, int] | None:
    box = getattr(mark, "bbox", None)
    return tuple(box.center) if box is not None else None  # type: ignore[return-value]


def diagnose(
    *,
    attempted: Any,
    fresh_marks: list[Any],
    goal: str,
    world_changed: bool,
    alternative_finder: Callable[[list[Any], str], Any] | None = None,
) -> Diagnosis:
    """Classify a failure from what can be observed after it.

    ``attempted`` is the mark the agent acted on, ``fresh_marks`` is what a new
    observation now yields, and ``world_changed`` says whether acting altered
    the page at all. No argument names the injected fault.
    """
    evidence: list[str] = []
    label = _label_of(attempted)
    before = _centre_of(attempted)

    same_label = [m for m in fresh_marks if _label_of(m) == label]
    evidence.append(f"re-observed the page: {len(fresh_marks)} interactive elements")
    evidence.append(f"looked for the element it acted on ({label!r}): {'found' if same_label else 'gone'}")

    if same_label:
        after = _centre_of(same_label[0])
        evidence.append(f"its position then {before}, now {after}")
        if before is not None and after is not None and before != after:
            # It is still on the page but somewhere else, so the plan was stale
            # rather than wrong. Looking again is enough.
            return Diagnosis(
                cause=CAUSE_MOVED,
                strategy=STRATEGY_RETRY,
                tier=1,
                evidence=evidence,
                confidence=0.9,
            )

        # Still there, still in the same place, and acting changed nothing that
        # the goal cares about. Repeating the identical action would only repeat
        # the outcome, so this escalates rather than retries.
        evidence.append(f"acting changed the page: {'yes' if world_changed else 'no'}")
        if not world_changed:
            return Diagnosis(
                cause=CAUSE_INERT,
                strategy=STRATEGY_ESCALATE,
                tier=4,
                evidence=evidence
                + [
                    "the element reports success but the goal state did not follow",
                    "a second identical attempt would behave identically",
                ],
                confidence=0.75,
            )
        return Diagnosis(
            cause=CAUSE_OBSCURED,
            strategy=STRATEGY_ROLLBACK,
            tier=3,
            evidence=evidence + ["something changed, but not what the goal named"],
            confidence=0.55,
        )

    # The element is gone. Another route to the same goal may still exist, and
    # taking it is a reroute rather than a retry.
    alternative = alternative_finder(fresh_marks, goal) if alternative_finder else None
    if alternative is not None:
        evidence.append(f"searched for another way to reach the goal: found {_label_of(alternative)!r}")
        return Diagnosis(
            cause=CAUSE_VANISHED,
            strategy=STRATEGY_REROUTE,
            tier=2,
            evidence=evidence,
            alternative_label=_label_of(alternative),
            confidence=0.8,
        )

    evidence.append("searched for another way to reach the goal: none found")
    return Diagnosis(
        cause=CAUSE_VANISHED,
        strategy=STRATEGY_ESCALATE,
        tier=4,
        evidence=evidence + ["no remaining affordance can advance this goal"],
        confidence=0.85,
    )


__all__ = [
    "CAUSE_INERT",
    "CAUSE_MOVED",
    "CAUSE_OBSCURED",
    "CAUSE_UNKNOWN",
    "CAUSE_VANISHED",
    "Diagnosis",
    "STRATEGY_ESCALATE",
    "STRATEGY_REROUTE",
    "STRATEGY_RETRY",
    "STRATEGY_ROLLBACK",
    "diagnose",
]
