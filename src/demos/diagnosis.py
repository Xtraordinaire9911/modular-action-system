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
CAUSE_OCCLUDED = "target_occluded"
CAUSE_UNKNOWN = "undiagnosed"

STRATEGY_RETRY = "retry_from_fresh_observation"
STRATEGY_REROUTE = "reroute_to_alternative_affordance"
STRATEGY_CLEAR = "clear_the_obstruction_then_retry"
STRATEGY_ROLLBACK = "rollback_then_retry"
STRATEGY_ESCALATE = "escalate_to_human"

# One plain sentence per conclusion, shown next to the evidence so a viewer can
# judge the reasoning rather than take the label on trust.
CAUSE_EXPLANATION = {
    CAUSE_MOVED: "The control is still on the page, just not where it was when the plan "
    "was made. The plan went stale, so looking again is enough.",
    CAUSE_OCCLUDED: "The control is present and enabled, but something else is receiving "
    "the click. Repeating the click would hit the same obstruction, so the "
    "obstruction has to be dealt with first.",
    CAUSE_OBSCURED: "The control is visible and refuses input. Nothing is broken - a "
    "precondition simply is not met yet, so the answer is to satisfy it "
    "rather than to keep clicking.",
    CAUSE_INERT: "The action was accepted and the state the goal named did not follow. A "
    "second identical attempt would be accepted and ignored identically, so "
    "retrying would only waste it.",
    CAUSE_VANISHED: "The control the plan named is no longer in the document, so no amount "
    "of retrying can reach it. Either another route exists, or the goal "
    "cannot be met from here.",
    CAUSE_UNKNOWN: "The evidence does not distinguish between the possible causes, so "
    "acting on a guess would be worse than handing over.",
}


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
        lines = ["what the agent measured:"]
        lines += [f"  - {item}" for item in self.evidence]
        lines += [
            "",
            f"conclusion: {self.cause}",
            "",
            CAUSE_EXPLANATION.get(self.cause, ""),
            "",
            f"strategy:   {self.strategy}  (tier {self.tier})",
            f"confidence: {self.confidence:.2f}",
        ]
        if self.alternative_label:
            lines.append(f"alternative: {self.alternative_label!r}")
        return "\n".join(lines)

    @property
    def reasoning(self) -> str:
        """The one-sentence account of why this conclusion follows."""
        return CAUSE_EXPLANATION.get(self.cause, "")

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


def diagnose_with_probes(
    observation: Any, *, moved: bool, still_present: bool, alternative_label: str = ""
) -> Diagnosis:
    """Reach a conclusion from measurements rather than from two coordinates.

    Order matters here, and it follows what can be ruled out most firmly:

    1. If the click point is held by another element, the target was never
       reached - that is decided by a hit test, not by inference.
    2. If the target refuses input, no amount of aiming would have helped.
    3. If it simply sits elsewhere, the plan was stale.
    4. If it is gone, the question becomes whether another route exists.
    5. If it is present, reachable and unmoved, the action was accepted and
       did not take effect - the one case where retrying is provably useless.
    """
    evidence = list(observation.evidence())

    # Nothing was actually measured, so any conclusion would be invented.
    if not (observation.hit.ok or observation.interact.ok or observation.occlusion.ok):
        return Diagnosis(
            cause=CAUSE_UNKNOWN,
            strategy=STRATEGY_ESCALATE,
            tier=4,
            evidence=evidence + ["no probe could run, so nothing was measured"],
            confidence=0.2,
        )

    if observation.occlusion.ok and observation.occlusion.covered:
        return Diagnosis(
            cause=CAUSE_OCCLUDED,
            strategy=STRATEGY_CLEAR,
            tier=2,
            evidence=evidence,
            alternative_label=observation.occlusion.coverer_text.strip()[:40],
            confidence=0.9,
        )

    if observation.interact.ok and observation.interact.exists and not observation.interact.actionable:
        return Diagnosis(
            cause=CAUSE_OBSCURED,
            strategy=STRATEGY_ROLLBACK,
            tier=3,
            evidence=evidence,
            confidence=0.85,
        )

    # A target that has moved only explains the failure if the click missed it.
    # When the hit test says the intended element received the click, the action
    # was delivered, and where the element sits now is a consequence of the page
    # reacting - not the reason nothing happened.
    delivered = observation.hit.ok and observation.hit.is_target
    if still_present and moved and not delivered:
        return Diagnosis(
            cause=CAUSE_MOVED,
            strategy=STRATEGY_RETRY,
            tier=1,
            evidence=evidence + ["the click did not land on the intended element"],
            confidence=0.9,
        )

    if not still_present:
        if alternative_label:
            return Diagnosis(
                cause=CAUSE_VANISHED,
                strategy=STRATEGY_REROUTE,
                tier=2,
                evidence=evidence,
                alternative_label=alternative_label,
                confidence=0.8,
            )
        return Diagnosis(
            cause=CAUSE_VANISHED,
            strategy=STRATEGY_ESCALATE,
            tier=4,
            evidence=evidence + ["no remaining affordance can advance this goal"],
            confidence=0.85,
        )

    # Present, reachable, in the same place, and the goal state still absent.
    # Whether the region reverted to a rejection message or never moved at all,
    # the action was accepted and did not take effect, so a repeat of it would
    # be accepted and ignored the same way.
    detail = (
        "the region changed, but not to the state the goal named"
        if observation.region_changed
        else "the region did not change at all"
    )
    return Diagnosis(
        cause=CAUSE_INERT,
        strategy=STRATEGY_ESCALATE,
        tier=4,
        evidence=evidence + [detail, "a second identical attempt would behave identically"],
        confidence=0.7 if observation.region_changed else 0.8,
    )


__all__ = [
    "CAUSE_EXPLANATION",
    "CAUSE_INERT",
    "CAUSE_OCCLUDED",
    "STRATEGY_CLEAR",
    "diagnose_with_probes",
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
