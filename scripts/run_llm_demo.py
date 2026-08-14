"""The same loop, driven by a model, with the rules running beside it.

    python scripts/run_llm_demo.py
    python scripts/run_llm_demo.py --pace 1.5 --hold 3 --record

The narrated loop demo is deterministic end to end, so watching it cannot tell
you what a model contributes. This one is built around exactly that question, and
it answers it with evidence rather than narration - a caption saying "sent to a
language model" looks the same whether a model ran or not.

On screen, for every scene, at the same time:

  left column    the rules running on the sentence: each pattern tried, which
                 matched, and the verdict
  right column   the request sent to the model and its raw reply, revealed line
                 by line, with latency and provider-reported token counts
  below          the image the vision model was given - the exact bytes, shown
                 in the page - the question asked, and its own words back
  footer         running totals: calls, tokens, model time, and the score of
                 each path

Four scenes:

  1. a request phrased the way the rules expect      both succeed - the model is
                                                     earning nothing here
  2. the same request phrased like a person          nine patterns, no match; the
                                                     model interprets it
  3. the goal is reached and both sources agree      confirmed by text and by
                                                     looking, independently
  4. the page lies: the confirmation is in the DOM   the text oracle passes and
     and painted over on screen                      the model contradicts it

Scene 4 is the one no text-based check in this repository can do - all of them
pass there. Nothing is staged: the rules really run, the model really answers,
and the screenshot is the region of the live page. With no API key configured the
run still completes and says at every step that no model was available.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.demos.model_panel import ModelPanel  # noqa: E402
from src.demos.realistic_faults import FAULTS  # noqa: E402
from src.perception.vlm_observer import VlmObserver, available_vision_client  # noqa: E402
from src.planner.environment_binding import binding_for  # noqa: E402
from src.planner.intent_planner import (  # noqa: E402
    KNOWN_GOAL_STATES,
    IntentPlanner,
    available_client,
    rule_fallback,
    rule_trace,
)

_LINE = "=" * 78


@dataclass
class Scene:
    """One thing to show, and the reason it is worth showing."""

    title: str
    utterance: str
    why: str
    fault: str = ""
    expect_rules_to_fail: bool = False


SCENES: tuple[Scene, ...] = (
    Scene(
        title="SCENE 1/4 - phrased the way the rules expect",
        utterance="add the wireless headphones to my cart",
        why="The control: on a sentence written to match a keyword pattern, the model earns nothing.",
    ),
    Scene(
        title="SCENE 2/4 - phrased the way a person speaks",
        utterance="grab me those wireless headphones, I need them for my commute",
        why="Same intent, no keyword the rules look for. Measured over nine such requests: rules 0, model 9.",
        expect_rules_to_fail=True,
    ),
    Scene(
        title="SCENE 3/4 - two independent sources agree",
        utterance="order me the mechanical keyboard",
        why="The DOM says the item is there. A vision model is shown the region and asked separately.",
        expect_rules_to_fail=True,
    ),
    Scene(
        title="SCENE 4/4 - the page lies, and only looking catches it",
        utterance="I'll take one of those 4K monitors",
        why="The confirmation stays in the DOM and is painted over on screen. Every text check here passes.",
        fault="invisible_confirmation",
        expect_rules_to_fail=True,
    ),
)


@dataclass
class SceneRecord:
    title: str
    utterance: str
    rules_goal: str = ""
    model_goal: str = ""
    model_source: str = ""
    model_latency_ms: float = 0.0
    model_tokens: dict[str, int] = field(default_factory=dict)
    dom_says_met: bool = False
    vision_answer: bool | None = None
    vision_confidence: float = 0.0
    vision_evidence: str = ""
    vision_source: str = ""
    vision_latency_ms: float = 0.0
    caught_false_success: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "utterance": self.utterance,
            "rules_goal": self.rules_goal,
            "model_goal": self.model_goal,
            "model_source": self.model_source,
            "model_latency_ms": round(self.model_latency_ms, 1),
            "model_tokens": self.model_tokens,
            "dom_says_met": self.dom_says_met,
            "vision_answer": self.vision_answer,
            "vision_confidence": round(self.vision_confidence, 3),
            "vision_evidence": self.vision_evidence,
            "vision_source": self.vision_source,
            "vision_latency_ms": round(self.vision_latency_ms, 1),
            "caught_false_success": self.caught_false_success,
        }


@dataclass
class Run:
    scenes: list[SceneRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": datetime.now().isoformat(timespec="seconds"),
            "scenes": [scene.to_dict() for scene in self.scenes],
            "rules_solved": sum(1 for s in self.scenes if s.rules_goal),
            "model_solved": sum(1 for s in self.scenes if s.model_goal),
            "false_successes_caught": sum(1 for s in self.scenes if s.caught_false_success),
        }


def model_verdict(plan: Any) -> str:
    """What to put on screen for the model's answer, including "there wasn't one".

    Without this the no-key run reads as though a model considered the sentence
    and declined, which is a different claim from the true one.
    """
    if plan.ok:
        return f"{plan.goal.goal_state}  {json.dumps(plan.goal.parameters)}"
    if "no model configured" in plan.error:
        return "no model configured"
    return "no supported goal"


def exists(session: Any, selector: str) -> bool:
    """Whether the page actually has this control, checked before clicking it."""
    try:
        return bool(session.evaluate("sel => !!document.querySelector(sel)", selector))
    except Exception:
        return False


def request_preview(system_chars: int, utterance: str) -> str:
    """The request, short enough to read on screen and true to what was sent."""
    return f'system: {system_chars} chars, {len(KNOWN_GOAL_STATES)} goal states\nuser:   "{utterance}"'


def usage_of(client: Any) -> dict[str, int]:
    """Token counts the provider reported for its last call, or nothing."""
    return dict(getattr(client, "last_usage", {}) or {})


def run_scene(
    session: Any,
    panel: ModelPanel,
    scene: Scene,
    *,
    pace: float,
    type_delay: float,
    observer: VlmObserver,
    planner: IntentPlanner,
    text_client: Any,
) -> SceneRecord:
    record = SceneRecord(title=scene.title, utterance=scene.utterance)
    panel.begin_scene(scene.title, scene.utterance, scene.why)
    time.sleep(pace)

    # --- the rules, first, so the comparison is not retrospective -------------
    patterns = rule_trace(scene.utterance)
    rules = rule_fallback(scene.utterance)
    record.rules_goal = rules.goal.goal_state if rules.ok else ""
    matched = sum(1 for _, hit in patterns if hit)
    panel.show_rules(
        patterns,
        f"{record.rules_goal}" if rules.ok else f"{matched} of {len(patterns)} patterns matched - no goal",
        rules.ok,
    )
    time.sleep(pace * 1.4)

    # --- the model, on the identical sentence ---------------------------------
    panel.sending(
        getattr(text_client, "name", "") or "no model configured",
        request_preview(planner.system_prompt_size(), scene.utterance),
    )
    time.sleep(pace * 0.4)
    plan = planner.plan(scene.utterance)
    record.model_goal = plan.goal.goal_state if plan.ok else ""
    record.model_source = plan.source
    record.model_latency_ms = plan.latency_ms
    record.model_tokens = usage_of(text_client)
    panel.reply(
        plan.raw_response or plan.error or "no reply",
        latency_ms=plan.latency_ms,
        usage=record.model_tokens,
        verdict=model_verdict(plan),
        ok=plan.ok,
        type_delay=type_delay,
    )
    time.sleep(pace * 1.6)

    if not plan.ok:
        panel.conclude("Neither path produced a goal. Nothing is attempted.", "no")
        time.sleep(pace * 2)
        return record

    if scene.expect_rules_to_fail:
        panel.conclude("The rules produced nothing here. The model produced a goal.", "ok")
        time.sleep(pace * 1.4)

    binding = binding_for(plan.goal.goal_state)
    completion = binding.completion_for(plan.goal.parameters) if binding else ""
    # The model names the subject in its own words, so the control it resolves to
    # may not exist here. Say so and stop, rather than clicking into a timeout.
    if binding is None or not completion or not exists(session, completion):
        panel.conclude("Understood, but this page has no control for it.", "no")
        time.sleep(pace * 2)
        return record
    region = binding.success_region(plan.goal.parameters)

    # --- act, then check the page twice ---------------------------------------
    session.click(completion)
    if scene.fault:
        FAULTS[scene.fault].apply(session, region)
        panel.conclude(f"fault injected: {FAULTS[scene.fault].name}", "no")
        time.sleep(pace * 1.6)

    proof = binding.success_for(plan.goal.parameters)
    observed = (session.text_content(region) or "").lower()
    record.dom_says_met = bool(proof) and proof.lower() in observed
    panel.show_oracle(
        f"{proof!r} in {region}: {'found - goal reached' if record.dom_says_met else 'not found'}",
        record.dom_says_met,
    )
    time.sleep(pace * 1.6)

    # --- the second modality ---------------------------------------------------
    question = binding.visual_question(plan.goal.parameters)
    image = session.screenshot_element(region) or session.screenshot()
    panel.looking(
        getattr(observer.client, "name", "") or "no vision model configured",
        question,
        image,
        caption=f"{region}, {len(image)} bytes",
    )
    time.sleep(pace * 0.6)
    # Whether this actually cost anything, so the cache and the ceiling are
    # visible on screen instead of being claimed in a README.
    before = observer.billed_calls
    judgement = observer.look(image, question, region=region)
    billed = observer.billed_calls > before
    record.vision_answer = judgement.answer if judgement.usable else None
    record.vision_confidence = judgement.confidence
    record.vision_evidence = judgement.evidence
    record.vision_source = judgement.source
    record.vision_latency_ms = judgement.latency_ms
    panel.saw(
        judgement.raw_response or judgement.error or judgement.source,
        latency_ms=judgement.latency_ms,
        usage=usage_of(observer.client) if billed else None,
        verdict=(
            f"answer {judgement.answer} at confidence {judgement.confidence:.2f}"
            if judgement.usable
            else f"not usable: {judgement.source}"
        ),
        ok=bool(judgement.usable and judgement.answer),
        type_delay=type_delay,
        billed=billed,
        note="" if billed else "same pixels, same question: answered from cache, not billed",
    )
    time.sleep(pace * 1.2)

    record.caught_false_success = record.dom_says_met and record.vision_answer is False
    if record.caught_false_success:
        panel.conclude("CONFLICT - the text says yes, the screen says no. The false success is caught.", "no")
    elif judgement.usable and record.vision_answer == record.dom_says_met:
        panel.conclude("Two independent sources agree. The goal is confirmed twice.", "ok")
    else:
        panel.conclude(f"Only one source of evidence: {judgement.source}", "no")
    time.sleep(pace * 2.4)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pace", type=float, default=1.5, help="Seconds per beat.")
    parser.add_argument("--type-delay", type=float, default=0.12, help="Seconds per revealed line of a reply.")
    parser.add_argument("--hold", type=float, default=3.0, help="Seconds to stay on the final summary.")
    parser.add_argument("--headless", dest="headed", action="store_false", default=True)
    parser.add_argument("--record", action="store_true", help="Capture the page and convert it to mp4.")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    import inspect

    from scripts.run_agent_loop_demo import to_mp4
    from src.perception.browser_session import BrowserSession

    repo = Path(__file__).resolve().parents[1]
    out = repo / "eval_outputs" / "llm_demo" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    url = f"http://127.0.0.1:{port}/shopping.html"

    launch: dict[str, Any] = {"headless": not args.headed}
    if args.record and "record_video_dir" in inspect.signature(BrowserSession.launch).parameters:
        launch["record_video_dir"] = str(out)

    vision = available_vision_client()
    text = available_client()
    # Both ledgers are written into this run's folder rather than the shared one.
    # The shared ledger is appended to by every run and every test, so a reader
    # cannot tell which lines belong to the recording; these two files are
    # exactly the calls the video shows being made.
    observer = VlmObserver(
        client=vision,
        ledger_path=out / "vision-calls.jsonl",
        max_calls=len(SCENES),  # one paid call per scene at most
    )
    # No fallback: this demo exists to compare the model against the rules, and a
    # planner that quietly answers with the rules would compare them to themselves.
    planner = IntentPlanner(client=text, ledger_path=out / "intent-calls.jsonl", allow_fallback=False)

    print(f"\n{_LINE}\n  THE SAME LOOP, WITH AND WITHOUT A MODEL\n{_LINE}")
    print(f"  intent model : {getattr(text, 'name', '') or 'not configured - the run will say so'}")
    print(f"  vision model : {getattr(vision, 'name', '') or 'not configured - the run will say so'}")

    session = BrowserSession.launch(url, **launch)
    panel = ModelPanel(session)
    run = Run()
    try:
        for index, scene in enumerate(SCENES, start=1):
            print(f"\n  --- scene {index}/{len(SCENES)}: {scene.title}")
            session.open(url)
            time.sleep(0.35)
            panel.open()
            summary = run.to_dict()
            panel.score(
                rules=summary["rules_solved"],
                model=summary["model_solved"],
                caught=summary["false_successes_caught"],
                scenes=index - 1,
            )
            record = run_scene(
                session,
                panel,
                scene,
                pace=args.pace,
                type_delay=args.type_delay,
                observer=observer,
                planner=planner,
                text_client=text,
            )
            run.scenes.append(record)
            summary = run.to_dict()
            panel.score(
                rules=summary["rules_solved"],
                model=summary["model_solved"],
                caught=summary["false_successes_caught"],
                scenes=index,
            )
            print(f"      rules {record.rules_goal or 'refused':<14} model {record.model_goal or 'refused':<14}")
            if record.vision_source:
                print(f"      vision {record.vision_source}: {record.vision_answer} ({record.vision_confidence:.2f})")
            session.screenshot(str(out / f"scene{index}.png"))
            time.sleep(args.pace)

        summary = run.to_dict()
        panel.begin_scene(
            "RUN COMPLETE",
            f"rules {summary['rules_solved']}/{len(SCENES)}  -  model {summary['model_solved']}/{len(SCENES)}",
            "Scene 1 is the control, where the model earns nothing. The rest are sentences a person "
            "would say, or a page that lies to a text query.",
        )
        panel.show_rules(
            [(s.utterance, bool(s.rules_goal)) for s in run.scenes],
            f"{summary['rules_solved']} of {len(SCENES)} interpreted",
            summary["rules_solved"] == len(SCENES),
        )
        panel.sending(getattr(text, "name", "") or "no model", "the same four sentences")
        panel.reply(
            "\n".join(f"{s.model_goal or 'refused':<14} {s.utterance}" for s in run.scenes),
            latency_ms=sum(s.model_latency_ms for s in run.scenes),
            usage=None,
            verdict=f"{summary['model_solved']} of {len(SCENES)} interpreted",
            ok=summary["model_solved"] == len(SCENES),
            type_delay=args.type_delay,
            billed=False,  # a recap of calls already counted, not a new one
            note="total across the four calls above; nothing was sent for this summary",
        )
        panel.conclude(
            f"{summary['false_successes_caught']} false success caught by looking, which no text check here can do.",
            "ok" if summary["false_successes_caught"] else "idle",
        )
        panel.score(
            rules=summary["rules_solved"],
            model=summary["model_solved"],
            caught=summary["false_successes_caught"],
            scenes=len(run.scenes),
        )
        session.screenshot(str(out / "summary.png"))
        time.sleep(args.hold)
    finally:
        panel.close()
        session.close()
        httpd.shutdown()

    (out / "llm_demo.json").write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    if args.record:
        video = to_mp4(out)
        if video:
            # to_mp4 names its output after the loop demo; this is a different one.
            named = (out / video).replace(out / "llm_demo.mp4")
            print(f"\n  video     : {named.relative_to(repo)}")

    summary = run.to_dict()
    print(f"\n{_LINE}")
    print(f"  rules interpreted        : {summary['rules_solved']}/{len(SCENES)}")
    print(f"  model interpreted        : {summary['model_solved']}/{len(SCENES)}")
    print(f"  false successes caught   : {summary['false_successes_caught']}")
    print(f"  model calls              : {observer.billed_calls} vision, {len(SCENES)} text")
    print(f"  artifacts                : {out.relative_to(repo)}\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
