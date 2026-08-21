"""Resolve a perceptual conflict by looking at the disputed region again.

When two sources disagree about the same fact, the runtime does not pick a
winner: it stops and asks for a fresh observation. Until now the only probe
behind that request was a stub in the tests, so the escalation path existed and
nothing could actually resolve anything. A conflict between the DOM and a visual
reading is exactly the case where looking again should settle it, and a vision
model is the thing that can look.

So this is an :class:`~src.verification.active_perception.ActivePerceptionProbe`
whose fresh evidence comes from a screenshot taken *now*, of the region the
conflict is about, judged by a model.

Three properties, and each is a way this could have quietly cheated:

* **The screenshot is taken during the probe, not reused.** That is what makes
  the evidence fresh; a cached image would make the whole re-observation
  ceremonial.
* **The returned observation is built, not borrowed.** The established probe
  contract is to return a *fresh* observation, so returning the original with an
  assertion appended would present stale DOM state as newly observed. Where the
  caller supplies a way to re-read the environment, that is used; where it does
  not, only what was actually observed here is returned, and the provenance says
  so.
* **A model that cannot answer resolves nothing.** An unusable judgement
  contributes no assertion, and a probe with nothing to contribute returns None -
  which the resolver already treats as "re-observation failed" and escalates.
  Returning an empty observation instead would look like a successful look that
  happened to find nothing, which is a different and false claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from src.contracts.types import Observation, ObservedAssertion
from src.runtime.cognitive_map import CognitiveMap, Conflict


class Screenshotter(Protocol):
    """Just enough of a browser session to photograph one region."""

    def screenshot_element(self, selector: str) -> bytes: ...


class VisionObserver(Protocol):
    """Just enough of :class:`~src.perception.vlm_observer.VlmObserver`."""

    def look(self, image_png: bytes, question: str, *, region: str = ...) -> Any: ...


@dataclass(frozen=True)
class VisualCheck:
    """Where to look for one disputed fact, and what to ask about it.

    Declared per entity and attribute rather than derived from them: a booking
    status is a line of text and a vote button is a filled triangle, and a
    question phrased for one gets a confidently wrong answer about the other.
    """

    region: str  # the element to photograph
    claim: str  # completed into "Does this image show ...?"

    def question(self, conflict: Conflict) -> str:
        disputed = ", ".join(f"{source}={value!r}" for source, value in sorted(conflict.values.items()))
        asked = self.claim.format(entity=conflict.entity_id, attribute=conflict.attribute)
        suffix = f" Sources disagree: {disputed}." if disputed else ""
        return f"Does this image show {asked}?{suffix} Answer from the image only."


# The smart room's disputed facts, keyed the way a conflict names them. Kept here
# rather than inside the probe so a different environment supplies its own table
# instead of this module growing to know about every surface.
SMART_ROOM_CHECKS: dict[tuple[str, str], VisualCheck] = {
    ("room", "booked"): VisualCheck(
        region="[data-testid='booking-status']",
        claim="a booking confirmation naming a room and a time",
    ),
    ("thermostat", "at_target"): VisualCheck(
        region="[data-testid='thermostat-panel']",
        claim="a thermostat whose Current reading has reached its Target",
    ),
    ("thermostat", "target_temperature"): VisualCheck(
        region="[data-testid='thermostat-panel']",
        claim="a thermostat panel whose Target and Current readings agree",
    ),
    ("lights", "at_brightness"): VisualCheck(
        region="[data-testid='lighting-panel']",
        claim="a lighting panel showing the requested brightness",
    ),
    ("projector", "powered_on"): VisualCheck(
        region="[data-testid='projector-panel']",
        claim="a projector panel whose Power reads on",
    ),
    ("room", "ready"): VisualCheck(
        region="[data-testid='readiness-panel']",
        claim="a readiness panel reading READY",
    ),
}


@dataclass
class VlmPerceptionProbe:
    """Answer a conflict with a fresh look at the region it is about."""

    observer: VisionObserver
    screenshot: Screenshotter
    checks: dict[tuple[str, str], VisualCheck] = field(default_factory=lambda: dict(SMART_ROOM_CHECKS))
    # How to re-read the rest of the environment. Supplied by the caller because
    # this module can photograph a region but has no business knowing how to
    # rebuild an observation. Without it, only the visual channel is fresh, and
    # `looked_at` on the result records that this is all that was re-observed.
    reobserve: Callable[[], Observation | None] | None = None
    judgements: list[Any] = field(default_factory=list)

    def check_for(self, conflict: Conflict) -> VisualCheck | None:
        return self.checks.get((conflict.entity_id, conflict.attribute))

    async def observe(
        self,
        conflicts: list[Conflict],
        cognitive_map: CognitiveMap,
        original_observation: Observation,
    ) -> Observation | None:
        assertions: list[ObservedAssertion] = []
        looked_at: list[str] = []

        for conflict in conflicts:
            check = self.check_for(conflict)
            if check is None:
                # Nothing declares how to see this fact. Skipped rather than
                # guessed at: a screenshot of the wrong region answered
                # confidently is worse than no answer.
                continue
            try:
                image = self.screenshot.screenshot_element(check.region)
            except Exception:  # a region that cannot be photographed is not evidence
                continue
            if not image:
                continue

            judgement = self.observer.look(image, check.question(conflict), region=check.region)
            self.judgements.append(judgement)
            looked_at.append(check.region)

            assertion = getattr(judgement, "as_assertion", None)
            if assertion is None:
                continue
            observed = assertion(conflict.entity_id, conflict.attribute)
            if observed is not None:
                assertions.append(observed)

        if not assertions:
            # Nothing usable came back. The resolver reads None as "the probe
            # could not re-observe" and escalates, which is the honest outcome:
            # an empty observation would read as a look that found nothing.
            return None

        fresh = self.reobserve() if self.reobserve is not None else None
        if fresh is None:
            # Only the visual channel was re-read. Say that in the observation
            # rather than copying the original's DOM and letting it pass for new.
            return Observation(assertions=assertions, execution_history=[self._provenance(looked_at, partial=True)])

        fresh.assertions = [*fresh.assertions, *assertions]
        fresh.execution_history = [*fresh.execution_history, self._provenance(looked_at, partial=False)]
        return fresh

    def _provenance(self, looked_at: list[str], *, partial: bool) -> dict[str, Any]:
        return {
            "action": "vlm_active_perception",
            "regions": list(looked_at),
            "channels_reobserved": ["visual"] if partial else ["visual", "environment"],
            "note": (
                "only the visual channel was re-observed; the caller supplied no way to re-read the environment"
                if partial
                else "the environment and the visual channel were both re-read"
            ),
        }


__all__ = ["SMART_ROOM_CHECKS", "Screenshotter", "VisionObserver", "VisualCheck", "VlmPerceptionProbe"]
