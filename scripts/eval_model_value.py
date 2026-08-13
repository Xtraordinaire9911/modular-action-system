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
    # Control group: the fallback handles these, so the model must not regress.
    Utterance("add the wireless headphones to my cart", "rules_already_handle", "item_in_cart"),
    Utterance("upvote the top post", "rules_already_handle", "post_upvoted"),
    # A model that agrees with everything is not understanding anything.
    Utterance("make me a sandwich", "out_of_scope", ""),
    Utterance("book me a flight to Lisbon next Tuesday", "out_of_scope", ""),
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


@dataclass
class VisionTrial:
    condition: str  # clean | invisible_confirmation
    dom_says_met: bool
    model_says_met: bool | None  # None when the model gave no usable answer
    confidence: float = 0.0
    evidence: str = ""
    source: str = ""

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
        clean = [t for t in self.vision if t.condition == "clean"]
        faulted = [t for t in self.vision if t.condition != "clean"]
        answered_clean = [t for t in clean if t.model_says_met is not None]
        answered_faulted = [t for t in faulted if t.model_says_met is not None]
        return {
            "clean_trials": len(clean),
            "faulted_trials": len(faulted),
            "abstentions": sum(1 for t in self.vision if t.model_says_met is None),
            # On a clean page the DOM is right, so agreeing is correct.
            "agreement_rate": _rate(sum(1 for t in answered_clean if t.agrees), len(answered_clean)),
            # On a faulted page the DOM is wrong, so disagreeing is the catch.
            "detection_rate": _rate(sum(1 for t in answered_faulted if not t.agrees), len(answered_faulted)),
            # Crying wolf on a page that is fine.
            "false_alarm_rate": _rate(sum(1 for t in answered_clean if not t.agrees), len(answered_clean)),
        }


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


def run_vision_experiment(report: Report, *, reps: int, headed: bool) -> None:
    from src.perception.browser_session import BrowserSession

    binding = binding_for("item_in_cart")
    parameters = {"item": "wireless headphones"}
    question = binding.visual_question(parameters)
    observer = VlmObserver(client=available_vision_client(), max_calls=reps * 2 + 4)

    repo = Path(__file__).resolve().parents[1]
    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    url = f"http://127.0.0.1:{port}/shopping.html"
    try:
        for condition in ("clean", "invisible_confirmation"):
            for _ in range(reps):
                session = BrowserSession.launch(url, headless=not headed)
                try:
                    session.click(ADD_HEADPHONES)
                    if condition != "clean":
                        FAULTS[condition].apply(session, CART)
                    dom_text = (session.text_content(CART) or "").lower()
                    dom_says_met = EXPECTED_IN_CART in dom_text

                    image = session.screenshot_element(CART) or session.screenshot()
                    judgement = observer.look(image, question, region=f"{CART} [{condition}]")
                    report.vision.append(
                        VisionTrial(
                            condition=condition,
                            dom_says_met=dom_says_met,
                            model_says_met=judgement.answer if judgement.usable else None,
                            confidence=judgement.confidence,
                            evidence=judgement.evidence,
                            source=judgement.source,
                        )
                    )
                finally:
                    session.close()
    finally:
        httpd.shutdown()


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
        print(f"  agreement rate   (clean, DOM right) : {summary['agreement_rate']:.0%}")
        print(f"  detection rate   (fault, DOM wrong) : {summary['detection_rate']:.0%}")
        print(f"  false alarm rate (clean)            : {summary['false_alarm_rate']:.0%}")
        print(f"  abstentions                         : {summary['abstentions']}")

    out = Path("artifacts") / "model_value"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "intent": {"cases": [row.to_dict() for row in report.intent], "summary": report.intent_summary()},
        "vision": {"trials": [t.to_dict() for t in report.vision], "summary": report.vision_summary()},
    }
    (out / "model_value_report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  artifact : artifacts/model_value/model_value_report.json\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
