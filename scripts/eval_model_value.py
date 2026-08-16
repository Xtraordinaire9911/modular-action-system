"""Does adding a model earn its place? Two falsifiable experiments.

    python scripts/eval_model_value.py            # both experiments
    python scripts/eval_model_value.py --reps 5   # more repetitions for variance

Connecting a model and having it help are different achievements, and the first
one is easy to mistake for the second. Every earlier run here was a happy path
where the deterministic path was already right and the model agreed with it.
Agreement of that kind proves connectivity: a stub that always answered "yes"
would have passed all of it. So this measures the two things that would actually
justify the model being in the system, and reports numbers that can come out
against it.

**A. Does the intent layer understand anything the rules cannot?**

The rule fallback matches phrasings ("add ... cart", "upvote"). If a model only
handles the same sentences, it is decoration. So the set below is split: requests
that avoid those keywords entirely while meaning the same thing, requests the
fallback already handles (to catch regressions), and requests nothing here can
serve (to catch a model that agrees with everything).

Metric: coverage of each path on each group, and whether the model refuses what
it should refuse.

**B. Can the visual check catch a false success the DOM confirms?**

This is the one that matters. The project's central claim is that executor
success is not task success, and its named failure is the false success. A DOM
oracle can be fooled: `invisible_confirmation` leaves the confirmation text in
the document and paints over the region, so every text-based check passes while a
person sees nothing. That failure is invisible to the first modality by
construction and visible to the second.

Metrics, each over `--reps` repetitions because a second source that changes its
mind is not a source:

  agreement rate   clean page, DOM right  -> the model should agree
  detection rate   faulted page, DOM wrong -> the model should disagree
  false alarm rate clean page             -> the model should not cry wolf

A model that scores high on agreement and low on detection is worse than no
model: it costs money and adds confidence to a wrong answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.demos.realistic_faults import FAULTS  # noqa: E402
from src.perception.vlm_observer import VlmObserver, available_vision_client  # noqa: E402
from src.planner.environment_binding import binding_for  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client, rule_fallback  # noqa: E402

_LINE = "=" * 78

CART = "#cart-items"
ADD_HEADPHONES = "button.add-cart-btn[data-id='headphones']"
EXPECTED_IN_CART = "headphones"


@dataclass(frozen=True)
class Utterance:
    """One request, and what a correct interpretation of it would be."""

    text: str
    group: str  # needs_interpretation | rules_already_handle | out_of_scope
    expected_goal: str  # "" when the correct answer is to refuse


UTTERANCES: tuple[Utterance, ...] = (
    # Same meaning, none of the fallback's keywords. If the model cannot do these
    # and the fallback can do everything else, the model is decoration.
    Utterance("I need the over-ear ones for my commute, grab them for me", "needs_interpretation", "item_in_cart"),
    Utterance("could you get me that laptop with the i7 in it", "needs_interpretation", "item_in_cart"),
    Utterance("the big 4k screen, I'll take one", "needs_interpretation", "item_in_cart"),
    Utterance("give the browser automation thread a thumbs up", "needs_interpretation", "post_upvoted"),
    Utterance("the top thread deserves some recognition", "needs_interpretation", "post_upvoted"),
    Utterance("I'll take one of those mechanical keyboards", "needs_interpretation", "item_in_cart"),
    Utterance("show the first discussion some appreciation", "needs_interpretation", "post_upvoted"),
    Utterance("order me the noise cancelling ones", "needs_interpretation", "item_in_cart"),
    Utterance("that automation post is good, give it a point", "needs_interpretation", "post_upvoted"),
    # Booking, which is what the demo actually runs on. Measuring the model only
    # on the shop and the forum while demonstrating it on the room would leave
    # the demonstrated capability the one with no evidence behind it. The
    # fallback needs both "book" and "room", so none of these reach it.
    Utterance("I need somewhere to present at 15:00, room B please", "needs_interpretation", "room_booked"),
    Utterance("hold room C for me at 16:00", "needs_interpretation", "room_booked"),
    Utterance("reserve room A for two o'clock", "needs_interpretation", "room_booked"),
    # Control group: the fallback handles these, so the model must not regress.
    Utterance("add the wireless headphones to my cart", "rules_already_handle", "item_in_cart"),
    Utterance("upvote the top post", "rules_already_handle", "post_upvoted"),
    Utterance("book room A at 14:00", "rules_already_handle", "room_booked"),
    # A model that agrees with everything is not understanding anything.
    Utterance("make me a sandwich", "out_of_scope", ""),
    # Two booking requests this agent cannot serve. They matter more since the
    # room_booked vocabulary was added: a model that had learned "booking words
    # mean room_booked" would take both, and the refusal is the only thing that
    # distinguishes understanding the capability from matching its keywords.
    Utterance("book me a flight to Lisbon next Tuesday", "out_of_scope", ""),
    Utterance("reserve a table for dinner at eight", "out_of_scope", ""),
    Utterance("what is the weather like in Munich", "out_of_scope", ""),
    Utterance("delete my account and everything in it", "out_of_scope", ""),
)


@dataclass
class IntentOutcome:
    utterance: str
    group: str
    expected_goal: str
    rules_goal: str = ""
    model_goal: str = ""
    model_source: str = ""

    @property
    def rules_correct(self) -> bool:
        return self.rules_goal == self.expected_goal

    @property
    def model_correct(self) -> bool:
        return self.model_goal == self.expected_goal

    def to_dict(self) -> dict[str, Any]:
        return {
            "utterance": self.utterance,
            "group": self.group,
            "expected_goal": self.expected_goal,
            "rules_goal": self.rules_goal,
            "model_goal": self.model_goal,
            "model_source": self.model_source,
            "rules_correct": self.rules_correct,
            "model_correct": self.model_correct,
        }


@dataclass(frozen=True)
class VisionCondition:
    """One page state, what a correct answer looks like, and how sure it should be.

    ``ambiguous`` is the column that decides whether the confidence number means
    anything. A model that reports the same certainty on a clear region and on a
    region cut off mid-word has a confidence field that carries no information,
    and any threshold built on it is decorative.
    """

    name: str
    click: str
    expected_answer: bool
    ambiguous: bool = False
    fault: str = ""


@dataclass
class VisionTrial:
    condition: str
    dom_says_met: bool
    model_says_met: bool | None  # None when the model gave no usable answer
    confidence: float = 0.0
    evidence: str = ""
    source: str = ""
    expected_answer: bool = True
    ambiguous: bool = False

    @property
    def model_correct(self) -> bool | None:
        return None if self.model_says_met is None else self.model_says_met == self.expected_answer

    @property
    def agrees(self) -> bool | None:
        return None if self.model_says_met is None else self.model_says_met == self.dom_says_met

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "dom_says_met": self.dom_says_met,
            "model_says_met": self.model_says_met,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "source": self.source,
            "expected_answer": self.expected_answer,
            "ambiguous": self.ambiguous,
            "model_correct": self.model_correct,
            "agrees": self.agrees,
        }


@dataclass
class Report:
    intent: list[IntentOutcome] = field(default_factory=list)
    vision: list[VisionTrial] = field(default_factory=list)

    def intent_summary(self) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for group in ("needs_interpretation", "rules_already_handle", "out_of_scope"):
            rows = [row for row in self.intent if row.group == group]
            if not rows:
                continue
            groups[group] = {
                "cases": len(rows),
                "rules_correct": sum(1 for r in rows if r.rules_correct),
                "model_correct": sum(1 for r in rows if r.model_correct),
            }
        return groups

    def vision_summary(self) -> dict[str, Any]:
        # Ambiguous trials are excluded from accuracy and detection on purpose.
        # A region cut off at "Wireless He" has no defensible right answer -
        # calling it False and grading against that was scoring the model
        # against my own arbitrary label, which dragged accuracy to 75% and
        # detection to 50% in a run where the model was never clearly wrong.
        # They are kept for what they can honestly measure: calibration, and
        # whether the model holds the same view twice.
        answered = [t for t in self.vision if t.model_says_met is not None and not t.ambiguous]
        clear = [t for t in self.vision if not t.ambiguous and t.confidence]
        murky = [t for t in self.vision if t.ambiguous and t.confidence]
        # "The DOM is wrong" is a property of the condition, not of the fault
        # label: wrong_item is faulted and the DOM is right about it. Defining
        # detection off the label counted that as a miss and reported 75% for a
        # run in which the model was never once wrong.
        dom_wrong = [t for t in answered if t.dom_says_met != t.expected_answer]
        dom_right = [t for t in answered if t.dom_says_met == t.expected_answer]
        return {
            "trials": len(self.vision),
            "accuracy": _rate(sum(1 for t in answered if t.model_correct), len(answered)),
            # The claim that justifies a second modality: when the first one is
            # wrong, does the second one say so.
            "detection_rate": _rate(sum(1 for t in dom_wrong if t.model_correct), len(dom_wrong)),
            # And when the first one is right, does the second one stay quiet.
            "false_alarm_rate": _rate(sum(1 for t in dom_right if not t.model_correct), len(dom_right)),
            "dom_wrong_trials": len(dom_wrong),
            "dom_right_trials": len(dom_right),
            # Whether the confidence number carries information at all.
            "mean_confidence_clear": round(sum(t.confidence for t in clear) / len(clear), 3) if clear else 0.0,
            "mean_confidence_ambiguous": round(sum(t.confidence for t in murky) / len(murky), 3) if murky else 0.0,
            # An answer that never arrived is a reliability cost, not an opinion.
            "no_answer": sum(1 for t in self.vision if t.model_says_met is None),
            "transport_errors": sum(1 for t in self.vision if t.source == "error"),
            "stability": self.stability(),
        }

    def stability(self) -> dict[str, float]:
        """Per condition, how often the model gave its own most common answer.

        1.0 means it never changed its mind across repetitions. This turned out
        to be the informative uncertainty signal: the model is perfectly stable
        where the evidence is clear and flips where it is not, while the
        confidence number barely moves either way.
        """
        by_condition: dict[str, list[bool]] = {}
        for trial in self.vision:
            if trial.model_says_met is not None:
                by_condition.setdefault(trial.condition, []).append(trial.model_says_met)
        result: dict[str, float] = {}
        for name, answers in by_condition.items():
            majority = max(answers.count(True), answers.count(False))
            result[name] = _rate(majority, len(answers))
        return result


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def run_intent_experiment(report: Report) -> None:
    planner = IntentPlanner(client=available_client())
    for case in UTTERANCES:
        rules = rule_fallback(case.text)
        model = planner.plan(case.text)
        report.intent.append(
            IntentOutcome(
                utterance=case.text,
                group=case.group,
                expected_goal=case.expected_goal,
                rules_goal=rules.goal.goal_state if rules.ok else "",
                model_goal=model.goal.goal_state if model.ok else "",
                model_source=model.source,
            )
        )


# Half the region covered, so the item name is cut off mid-word. Not a fault the
# agent has to survive - a probe for whether the confidence number moves when the
# evidence genuinely gets worse. Without a condition like this, "confidence 1.00"
# on everything is indistinguishable from a model that has no calibration at all.
_CLIP_JS = """(sel)=>{
    const el = document.querySelector(sel);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const cover = document.createElement('div');
    cover.style.cssText = `position:fixed;left:${r.left + r.width * 0.32}px;top:${r.top}px;
        width:${r.width * 0.68}px;height:${Math.max(r.height, 1)}px;
        background:#ffffff;z-index:8000;pointer-events:none`;
    document.body.appendChild(cover);
    return true;
}"""

VISION_CONDITIONS: tuple[VisionCondition, ...] = (
    # The DOM is right and the region shows the item.
    VisionCondition("clean", ADD_HEADPHONES, expected_answer=True),
    # The DOM is wrong: the text is in the document, the region is painted over.
    VisionCondition("invisible_confirmation", ADD_HEADPHONES, expected_answer=False, fault="invisible_confirmation"),
    # Two more ways the document can be right and the screen wrong. One class
    # would have been one trick; three is a claim about a family.
    VisionCondition("transparent_text", ADD_HEADPHONES, expected_answer=False, fault="transparent_text"),
    VisionCondition("offscreen_confirmation", ADD_HEADPHONES, expected_answer=False, fault="offscreen_confirmation"),
    # A different item is in the cart. A model that says yes to everything fails here.
    VisionCondition("wrong_item", "button.add-cart-btn[data-id='laptop']", expected_answer=False),
    # Genuinely hard to read. This is the calibration probe.
    VisionCondition("clipped_view", ADD_HEADPHONES, expected_answer=False, ambiguous=True, fault="__clip__"),
)


def run_vision_experiment(report: Report, *, reps: int, headed: bool) -> None:
    from src.perception.browser_session import BrowserSession

    binding = binding_for("item_in_cart")
    parameters = {"item": "wireless headphones"}
    question = binding.visual_question(parameters)
    observer = VlmObserver(client=available_vision_client(), max_calls=reps * len(VISION_CONDITIONS) + 4)

    repo = Path(__file__).resolve().parents[1]
    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    url = f"http://127.0.0.1:{port}/shopping.html"
    try:
        for condition in VISION_CONDITIONS:
            for index in range(reps):
                session = BrowserSession.launch(url, headless=not headed)
                try:
                    session.click(condition.click)
                    if condition.fault == "__clip__":
                        session.evaluate(_CLIP_JS, CART)
                    elif condition.fault:
                        FAULTS[condition.fault].apply(session, CART)

                    dom_text = (session.text_content(CART) or "").lower()
                    image = session.screenshot_element(CART) or session.screenshot()
                    # Vary the region label so the digest cache does not collapse
                    # repetitions into one answer: measuring stability needs the
                    # model asked again, not the previous reply handed back.
                    judgement = observer.look(image, question, region=f"{CART} [{condition.name} {index}]")
                    report.vision.append(
                        VisionTrial(
                            condition=condition.name,
                            dom_says_met=EXPECTED_IN_CART in dom_text,
                            model_says_met=judgement.answer if judgement.usable else None,
                            confidence=judgement.confidence,
                            evidence=judgement.evidence,
                            source=judgement.source,
                            expected_answer=condition.expected_answer,
                            ambiguous=condition.ambiguous,
                        )
                    )
                finally:
                    session.close()
    finally:
        httpd.shutdown()


def _previous_vision(path: Path) -> dict[str, Any] | None:
    """The vision measurement already on disk, if there is a real one.

    Carried forward with its own timestamp so an intent-only run does not erase
    evidence it did not gather. A previous run that was itself intent-only has
    nothing to carry, and says so rather than propagating an empty block.
    """
    try:
        previous = json.loads(path.read_text(encoding="utf-8")).get("vision", {})
    except (OSError, ValueError):
        return None
    if not previous.get("trials"):
        return None
    return {**previous, "carried_forward_from": "an earlier run; this run used --skip-vision"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per vision condition.")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--skip-vision", action="store_true")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    report = Report()

    print(f"\n{_LINE}\n  A. DOES THE INTENT LAYER UNDERSTAND WHAT THE RULES CANNOT?\n{_LINE}")
    run_intent_experiment(report)
    print(f"  {'request':<52} {'rules':>16} {'model':>16}")
    print(f"  {'-' * 86}")
    for row in report.intent:
        rules = row.rules_goal or "refused"
        model = row.model_goal or "refused"
        mark_r = "ok" if row.rules_correct else "XX"
        mark_m = "ok" if row.model_correct else "XX"
        print(f"  {row.utterance[:52]:<52} {mark_r} {rules:>13} {mark_m} {model:>13}")
    print()
    for group, stats in report.intent_summary().items():
        print(
            f"  {group:<24} rules {stats['rules_correct']}/{stats['cases']}"
            f"     model {stats['model_correct']}/{stats['cases']}"
        )

    if not args.skip_vision:
        print(f"\n{_LINE}\n  B. CAN THE VISUAL CHECK CATCH A FALSE SUCCESS THE DOM CONFIRMS?\n{_LINE}")
        run_vision_experiment(report, reps=args.reps, headed=args.headed)
        print(f"  {'condition':<26} {'dom':>6} {'model':>7} {'conf':>6}  evidence")
        print(f"  {'-' * 86}")
        for trial in report.vision:
            model = "abstain" if trial.model_says_met is None else str(trial.model_says_met)
            print(
                f"  {trial.condition:<26} {str(trial.dom_says_met):>6} {model:>7} "
                f"{trial.confidence:>6.2f}  {trial.evidence[:34]}"
            )
        summary = report.vision_summary()
        print()
        print(f"  accuracy on answered trials          : {summary['accuracy']:.0%}")
        print(
            f"  detection rate (DOM wrong, n={summary['dom_wrong_trials']:<2})       : {summary['detection_rate']:.0%}"
        )
        print(
            f"  false alarm    (DOM right, n={summary['dom_right_trials']:<2})       : {summary['false_alarm_rate']:.0%}"
        )
        print(f"  mean confidence, clear conditions    : {summary['mean_confidence_clear']:.2f}")
        print(f"  mean confidence, ambiguous condition : {summary['mean_confidence_ambiguous']:.2f}")
        print(f"  no answer / transport errors         : {summary['no_answer']} / {summary['transport_errors']}")
        print()
        print("  stability across repetitions (1.00 = never changed its mind):")
        for name, value in summary["stability"].items():
            print(f"    {name:<26} {value:.2f}")
        print()
        print("  Accuracy and detection exclude the ambiguous condition: a region cut off")
        print("  mid-word has no defensible right answer, and grading against one would be")
        print("  scoring the model against a label of my own invention.")

    out = Path("artifacts") / "model_value"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "intent": {"cases": [row.to_dict() for row in report.intent], "summary": report.intent_summary()},
    }
    if args.skip_vision:
        # Writing a zeroed vision summary here would be worse than writing
        # nothing: the README cites this file for "detection 100%", and a reader
        # following that citation would find 0% and no trials, with no way to
        # tell a measurement of zero from a measurement that was never taken.
        # An intent-only run says so, and keeps the previous measurement with the
        # timestamp it was actually made at.
        previous = _previous_vision(out / "model_value_report.json")
        payload["vision"] = previous or {"not_measured": "this run used --skip-vision"}
    else:
        payload["vision"] = {
            "trials": [t.to_dict() for t in report.vision],
            "summary": report.vision_summary(),
        }
    (out / "model_value_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  artifact : artifacts/model_value/model_value_report.json\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
