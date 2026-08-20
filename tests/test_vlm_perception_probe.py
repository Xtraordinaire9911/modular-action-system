"""Re-observation has to actually re-observe, or the escalation path is theatre.

The resolver calls a probe when two sources disagree and treats None as "could
not re-observe". So the failure modes worth guarding are not wrong answers - the
arbiter handles those - but a probe that looks like it re-observed when it did
not: reusing a cached image, returning the original observation as though it were
fresh, or contributing an assertion a model never usably produced.
"""

from __future__ import annotations

import asyncio

from src.contracts.types import Observation, ObservedAssertion
from src.perception.vlm_perception_probe import SMART_ROOM_CHECKS, VisualCheck, VlmPerceptionProbe
from src.runtime.cognitive_map import CognitiveMap, Conflict


class _Shot:
    """Records every region photographed, so reuse is detectable."""

    def __init__(self, *, image: bytes = b"\x89PNG\r\n\x1a\npixels", fail: bool = False) -> None:
        self.image = image
        self.fail = fail
        self.regions: list[str] = []

    def screenshot_element(self, selector: str) -> bytes:
        if self.fail:
            raise RuntimeError("region not on the page")
        self.regions.append(selector)
        return self.image


class _Judgement:
    def __init__(self, *, usable: bool, answer: bool = True, confidence: float = 0.97) -> None:
        self._usable = usable
        self.answer = answer
        self.confidence = confidence
        self.source = "vlm" if usable else "low_confidence"

    def as_assertion(self, entity_id: str, attribute: str):
        if not self._usable:
            return None
        return ObservedAssertion(
            entity_id=entity_id,
            attribute=attribute,
            value=self.answer,
            source="visual",
            confidence=self.confidence,
        )


class _Observer:
    def __init__(self, *judgements: _Judgement) -> None:
        self._queue = list(judgements)
        self.questions: list[str] = []
        self.images: list[bytes] = []

    def look(self, image_png: bytes, question: str, *, region: str = ""):
        self.images.append(image_png)
        self.questions.append(question)
        return self._queue.pop(0) if self._queue else _Judgement(usable=False)


def _conflict(entity: str = "room", attribute: str = "booked") -> Conflict:
    return Conflict(
        conflict_type="source_disagreement",
        sources=["dom", "visual"],
        description=f"{entity}.{attribute} disagrees",
        id="c1",
        entity_id=entity,
        attribute=attribute,
        values={"dom": True, "visual": False},
    )


def _run(probe, conflicts, original=None):
    return asyncio.run(probe.observe(conflicts, CognitiveMap(task_id="t"), original or Observation(dom_tree="<old/>")))


# ── the look is real ─────────────────────────────────────────────────────────────


def test_the_region_is_photographed_during_the_probe():
    """A cached image would make the whole re-observation ceremonial."""
    shot = _Shot()
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True)), screenshot=shot)

    _run(probe, [_conflict()])

    assert shot.regions == ["[data-testid='booking-status']"]


def test_the_question_names_the_disagreement_it_is_settling():
    observer = _Observer(_Judgement(usable=True))
    probe = VlmPerceptionProbe(observer=observer, screenshot=_Shot())

    _run(probe, [_conflict()])

    asked = observer.questions[0]
    assert "dom=True" in asked and "visual=False" in asked
    assert "from the image only" in asked


def test_the_visual_evidence_reaches_the_observation_under_the_disputed_fact():
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True, answer=False)), screenshot=_Shot())

    fresh = _run(probe, [_conflict("thermostat", "at_target")])

    assert fresh is not None
    assertion = fresh.assertions[0]
    assert (assertion.entity_id, assertion.attribute) == ("thermostat", "at_target")
    assert assertion.source == "visual" and assertion.value is False


# ── nothing is borrowed ──────────────────────────────────────────────────────────


def test_without_a_way_to_re_read_the_environment_the_old_state_is_not_reused():
    """The contract is a *fresh* observation; copying the old DOM would fake one."""
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True)), screenshot=_Shot())

    fresh = _run(probe, [_conflict()], original=Observation(dom_tree="<stale/>"))

    assert fresh is not None
    assert fresh.dom_tree is None, "stale DOM was carried into an observation claiming to be fresh"
    entry = fresh.execution_history[0]
    assert entry["channels_reobserved"] == ["visual"]
    assert "only the visual channel" in entry["note"]


def test_when_the_caller_can_re_read_both_channels_are_fresh():
    probe = VlmPerceptionProbe(
        observer=_Observer(_Judgement(usable=True)),
        screenshot=_Shot(),
        reobserve=lambda: Observation(dom_tree="<new/>"),
    )

    fresh = _run(probe, [_conflict()], original=Observation(dom_tree="<stale/>"))

    assert fresh is not None and fresh.dom_tree == "<new/>"
    assert fresh.execution_history[-1]["channels_reobserved"] == ["visual", "environment"]
    assert any(a.source == "visual" for a in fresh.assertions)


def test_a_re_read_that_returns_nothing_falls_back_to_the_visual_channel_alone():
    probe = VlmPerceptionProbe(
        observer=_Observer(_Judgement(usable=True)),
        screenshot=_Shot(),
        reobserve=lambda: None,
    )

    fresh = _run(probe, [_conflict()])

    assert fresh is not None
    assert fresh.execution_history[0]["channels_reobserved"] == ["visual"]


# ── nothing is invented ──────────────────────────────────────────────────────────


def test_an_unusable_judgement_resolves_nothing():
    """None is what the resolver reads as "could not re-observe", and escalates."""
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=False)), screenshot=_Shot())

    assert _run(probe, [_conflict()]) is None


def test_a_fact_with_no_declared_way_to_see_it_is_skipped_not_guessed():
    shot = _Shot()
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True)), screenshot=shot)

    assert _run(probe, [_conflict("mystery_entity", "mystery_attribute")]) is None
    assert shot.regions == [], "a region was photographed for a fact nothing declares how to see"


def test_a_region_that_cannot_be_photographed_contributes_nothing():
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True)), screenshot=_Shot(fail=True))

    assert _run(probe, [_conflict()]) is None


def test_an_empty_image_is_not_sent_to_the_model():
    observer = _Observer(_Judgement(usable=True))
    probe = VlmPerceptionProbe(observer=observer, screenshot=_Shot(image=b""))

    assert _run(probe, [_conflict()]) is None
    assert observer.images == []


def test_several_conflicts_each_get_their_own_look():
    shot = _Shot()
    probe = VlmPerceptionProbe(observer=_Observer(_Judgement(usable=True), _Judgement(usable=True)), screenshot=shot)

    fresh = _run(probe, [_conflict("room", "booked"), _conflict("projector", "powered_on")])

    assert fresh is not None and len(fresh.assertions) == 2
    assert len(shot.regions) == 2 and len(set(shot.regions)) == 2


# ── the declared table ───────────────────────────────────────────────────────────


def test_every_declared_check_asks_about_its_own_region():
    """A question phrased for a text line gets a confident wrong answer about an
    icon, so each entry has to carry its own claim rather than a generic one."""
    claims = [check.claim for check in SMART_ROOM_CHECKS.values()]

    assert len(set(claims)) == len(claims), "two facts share a claim, so one of them is asked the wrong question"
    for (entity, attribute), check in SMART_ROOM_CHECKS.items():
        assert entity and attribute
        assert check.region.startswith("[data-testid=")


def test_a_caller_can_supply_its_own_table_for_another_surface():
    """The probe must not be the place that knows about every environment."""
    probe = VlmPerceptionProbe(
        observer=_Observer(_Judgement(usable=True)),
        screenshot=_Shot(),
        checks={("cart", "holds_item"): VisualCheck(region="#cart-items", claim="an entry in the cart")},
    )

    fresh = _run(probe, [_conflict("cart", "holds_item")])

    assert fresh is not None and fresh.assertions[0].entity_id == "cart"


# ── against the real resolver ────────────────────────────────────────────────────
# The tests above use conflicts this file constructs. That proves the probe's own
# properties and nothing about whether the resolver will call it or accept what it
# returns, so these two drive the real ActivePerceptionResolver and the real
# EpistemicArbiter.


def _seeded_map(pairs: list[tuple[str, bool]], *, entity: str = "thermostat", attribute: str = "at_target"):
    """A cognitive map holding one disputed fact, with fresh timestamps.

    The timestamps matter: an assertion with timestamp 0 is treated as ancient and
    is filtered out before conflicts are considered, so seeding without them
    produces no conflict at all and a test that appears to pass for the wrong
    reason.
    """
    import time

    now = int(time.time() * 1000)
    cognitive_map = CognitiveMap(task_id="resolver")
    cognitive_map.update_from_observation(
        Observation(
            assertions=[
                ObservedAssertion(
                    entity_id=entity,
                    attribute=attribute,
                    value=value,
                    source=source,
                    confidence=1.0,
                    timestamp_ms=now,
                )
                for source, value in pairs
            ]
        )
    )
    return cognitive_map


def test_the_resolver_uses_this_probe_to_settle_a_device_versus_screen_conflict():
    """The case the physical device layer makes possible, end to end.

    A Thing reports one value and the screen shows another. That reaches the
    default halt threshold, so the runtime stops and asks for a fresh look, and
    this probe is what answers.
    """
    from src.verification.active_perception import ActivePerceptionResolver
    from src.verification.conflict_detector import EpistemicArbiter

    cognitive_map = _seeded_map([("wot", True), ("visual", False)])
    arbiter = EpistemicArbiter()
    blocking = arbiter.check(cognitive_map)
    assert blocking, "the seeded disagreement should be blocking; the rest of this test is vacuous without it"

    # Looking again agrees with the device, so the disagreement goes away.
    probe = VlmPerceptionProbe(
        observer=_Observer(_Judgement(usable=True, answer=True)),
        screenshot=_Shot(),
    )

    result = asyncio.run(
        ActivePerceptionResolver(probe, arbiter=arbiter).resolve(blocking, cognitive_map, Observation())
    )

    assert result.resolved, f"the fresh look did not settle it: {result.trace}"
    assert probe.judgements, "the probe was never asked to look"


def test_a_screen_versus_dom_disagreement_does_not_block_with_the_default_arbiter():
    """Worth pinning, because it is the opposite of what the demo suggests.

    The case a vision model is most valuable for - the DOM says the goal is met
    and the screen shows nothing - carries a conflict mass of 0.8: the product of
    a boolean disagreement and the more reliable of the two sources, which is
    ``dom`` at 0.8. The default halt threshold is 1.0, so it never blocks, and
    active perception is therefore never invoked for it.

    So the false success in the demo is caught by the *episode's own* verification
    comparing the two answers, not by this probe. Anyone wiring visual conflict
    resolution into a runtime has to lower the threshold or raise the reliability
    of ``visual`` deliberately, and this test fails if those defaults change so
    the claim above cannot go stale unnoticed.
    """
    from src.verification.conflict_detector import EpistemicArbiter

    arbiter = EpistemicArbiter()
    assert arbiter.halt_threshold == 1.0
    assert arbiter.source_reliability["dom"] == 0.8
    assert arbiter.source_reliability["visual"] == 0.6

    assert arbiter.check(_seeded_map([("dom", True), ("visual", False)])) == []

    # The same disagreement, weighed without the threshold, to show what it is
    # worth rather than only that it is filtered.
    measured = EpistemicArbiter(halt_threshold=0.1).check(_seeded_map([("dom", True), ("visual", False)]))
    assert measured and abs(measured[0].conflict_mass - 0.8) < 1e-6

    # And it does block once a caller says visual evidence is worth trusting.
    trusting = EpistemicArbiter(source_reliability={"wot": 1.0, "dom": 0.8, "visual": 1.0, "system": 1.0})
    assert trusting.check(_seeded_map([("dom", True), ("visual", False)])) != []
