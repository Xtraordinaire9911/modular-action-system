"""The same loop, driven by a model, with the rules running beside it.

    docker compose -f env/docker-compose.yml up -d
    python scripts/run_llm_demo.py
    python scripts/run_llm_demo.py --pace 1.5 --hold 3 --record

The narrated loop demo is deterministic end to end, so watching it cannot tell
you what a model contributes. This one is built around exactly that question, and
it answers it with evidence rather than narration - a caption saying "sent to a
language model" looks the same whether a model ran or not.

It runs in the declared use case: the smart room. That matters for what is being
claimed, not only for consistency. The room has two surfaces, and an agent that
only ever worked on one of them would be an ordinary browser agent -

  the dashboard   the page a person actually uses, which the agent reads and
                  clicks like any other page
  the devices     the thermostat, blinds and projector, which no control on that
                  page can change. The agent resolves where to write from the
                  Thing Descriptions the room publishes, writes over WoT, and
                  then waits for the device to report having reached it

The second surface is a node-wot servient, not hardware. What it models is
timing and compliance: a setpoint is accepted at once and the room arrives
later, or - with a dead lamp or a jammed motor - never arrives while every
status code stays 2xx. That is the distinction the agent has to handle and the
one a page cannot produce. It models no thermodynamics and no hardware, and it
runs at 30x real time; `GET :8081/state` reports the scale. See the README for
what this is and is not evidence for.

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

  1. a booking phrased the way the rules expect      both succeed - the model is
                                                     earning nothing here
  2. the same booking phrased like a person          twelve patterns, no match;
                                                     the model interprets it
  3. a goal no control on the page can reach         resolved from the room's own
                                                     Thing Descriptions, written
                                                     over WoT, read back
  4. the dashboard lies: the confirmation is in      the text oracle passes and
     the DOM and painted over on screen              the model contradicts it

Scene 4 is the one no text-based check in this repository can do - all of them
pass there. Nothing is staged: the rules really run, the model really answers,
the write really reaches the device, and the screenshot is the region of the live
page. With no API key configured the run still completes and says at every step
that no model was available.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_room_prepared import reset_room  # noqa: E402
from src.demos.model_panel import ModelPanel  # noqa: E402
from src.demos.realistic_faults import FAULTS  # noqa: E402
from src.effectors.wot_executor import WotExecutor  # noqa: E402
from src.perception.td_affordance_parser import TdAffordanceParser  # noqa: E402
from src.perception.thing_directory import ThingDirectoryClient, ThingDirectoryError  # noqa: E402
from src.perception.vlm_observer import VlmObserver, available_vision_client  # noqa: E402
from src.planner.device_binding import (  # noqa: E402
    DeviceResolutionError,
    device_binding_for,
    resolve_device_target,
)
from src.planner.environment_binding import binding_for, device_view_for  # noqa: E402
from src.planner.intent_planner import (  # noqa: E402
    KNOWN_GOAL_STATES,
    IntentPlanner,
    available_client,
    rule_fallback,
    rule_trace,
)
from src.runtime.device_goal import values_match  # noqa: E402

DASHBOARD_URL = "http://localhost:3000"
DIRECTORY_URL = "http://localhost:8082"

_LINE = "=" * 78


@dataclass
class Scene:
    """One thing to show, and the reason it is worth showing."""

    title: str
    utterance: str
    why: str
    # Which goal this sentence is here to produce. Declared rather than inferred
    # so the suite can check that the room can actually serve it, and that the
    # four scenes between them touch both halves of the use case.
    expect_goal: str
    fault: str = ""
    expect_rules_to_fail: bool = False


SCENES: tuple[Scene, ...] = (
    Scene(
        title="SCENE 1/4 - phrased the way the rules expect",
        utterance="book room A at 14:00",
        why="The control: on a sentence written to match a keyword pattern, the model earns nothing.",
        expect_goal="room_booked",
    ),
    Scene(
        title="SCENE 2/4 - phrased the way a person speaks",
        utterance="I need somewhere to present at 15:00, room B please",
        why="Same intent, none of the twelve patterns match. Measured over nine such requests: rules 0, model 9.",
        expect_goal="room_booked",
        expect_rules_to_fail=True,
    ),
    Scene(
        title="SCENE 3/4 - a goal the page cannot reach",
        utterance="it's too cold, put it at 22 please",
        why="No control on this page can do it. The target is resolved from the Thing Descriptions "
        "the room publishes, written over WoT, and read back from the device.",
        expect_goal="temperature_set",
        expect_rules_to_fail=True,
    ),
    Scene(
        title="SCENE 4/4 - the dashboard lies, and only looking catches it",
        utterance="hold room C for me at 16:00",
        why="The confirmation stays in the DOM and is painted over on screen. Every text check here passes.",
        expect_goal="room_booked",
        fault="invisible_confirmation",
        expect_rules_to_fail=True,
    ),
)


@dataclass
class Room:
    """The devices the environment published, and the way to write to them.

    Discovery happens once per run rather than once per scene: which Things exist
    is a fact about the room, not about the sentence, and re-fetching it per scene
    would make the demo look like it re-derives the endpoint each time.
    """

    models: list[Any] = field(default_factory=list)
    executor: Any = None
    error: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.models) and self.executor is not None

    def titles(self) -> str:
        return ", ".join(m.title or m.thing_id for m in self.models) or "nothing"


@dataclass
class SceneRecord:
    title: str
    utterance: str
    rules_goal: str = ""
    model_goal: str = ""
    model_source: str = ""
    # Which half of the room this scene acted on. Recorded because the use case
    # is a digital surface over physical devices, and a run that only ever
    # touched one of them should not be able to look like it touched both.
    surface: str = ""
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
            "surface": self.surface,
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


def await_measurement(room: Room, resolved: Any, *, timeout: float) -> tuple[bool, float, Any]:
    """Wait for the device to report having reached what it was told.

    Returns whether it arrived, how long that took, and the last reading - the
    last reading because a value that stopped short is the interesting half of a
    physical failure, and reporting only "no" would throw it away.
    """
    started = time.monotonic()
    last: Any = None
    while time.monotonic() - started < timeout:
        try:
            last = room.executor.read_state(resolved.measured_source)
        except Exception:  # a read that fails is not an arrival
            last = None
        if last is not None and values_match(resolved.measured_value, last):
            return True, time.monotonic() - started, last
        time.sleep(0.15)
    return False, time.monotonic() - started, last


def await_text(session: Any, selector: str, wanted: str, *, timeout: float = 4.0) -> bool:
    """Wait until ``selector`` reads ``wanted``, or give up and let the check fail.

    Returning False rather than raising is deliberate: a value that never arrives
    is a result the run should report through its normal oracle, not an exception
    that ends the demo before the evidence is shown.
    """
    deadline = time.monotonic() + timeout
    needle = wanted.strip().lower()
    while time.monotonic() < deadline:
        if needle in (session.text_content(selector) or "").lower():
            return True
        time.sleep(0.2)
    return False


def reachable(url: str, timeout: float = 2.0) -> bool:
    """Whether something answers there, checked before the browser is launched.

    A missing room should be one clear sentence naming the compose command, not a
    Playwright timeout several seconds into a demo.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def discover_room(directory_url: str) -> Room:
    """Ask the directory which Things exist, and build the writer for them.

    A room that cannot be discovered is reported rather than assumed: the device
    scene then declines by name instead of writing to an address this code
    guessed.
    """
    try:
        client = ThingDirectoryClient(directory_url)
        tds = client.discover_tds()
        models = client.discover_models()
    except ThingDirectoryError as exc:
        return Room(error=str(exc))
    if not models:
        return Room(error=f"{directory_url} listed no Things")

    # The servient advertises the address it sees itself on, which inside compose
    # is a container IP nothing on the host can reach. Discovery looks healthy and
    # every write times out. Rewriting is reported on screen rather than done
    # quietly, because the environment is what needs fixing.
    base = advertised_base(tds)
    rewritten = ""
    if base and not reachable(base):
        split = urllib.parse.urlsplit(directory_url)
        target = f"{split.scheme or 'http'}://{split.hostname}:{urllib.parse.urlsplit(base).port or 8080}"
        tds = json.loads(json.dumps(tds).replace(base, target))
        models = [TdAffordanceParser().parse(td) for td in tds]
        rewritten = f"{base} -> {target}"
    room = Room(models=models, executor=WotExecutor(tds))
    room.error = rewritten
    return room


def advertised_base(tds: list[dict[str, Any]]) -> str:
    """The scheme://host:port the Thing Descriptions tell clients to write to."""
    for td in tds:
        for form in td.get("forms", []) or []:
            href = str(form.get("href", ""))
            if href.startswith("http"):
                split = urllib.parse.urlsplit(href)
                return f"{split.scheme}://{split.netloc}"
        for prop in (td.get("properties", {}) or {}).values():
            for form in prop.get("forms", []) or []:
                href = str(form.get("href", ""))
                if href.startswith("http"):
                    split = urllib.parse.urlsplit(href)
                    return f"{split.scheme}://{split.netloc}"
    return ""


def act_on_page(
    session: Any,
    panel: ModelPanel,
    plan: Any,
    *,
    pace: float,
) -> tuple[str, str, str] | None:
    """Do a goal the dashboard itself offers a control for.

    Returns ``(region, proof, question)``, or None when this page cannot do it.
    """
    binding = binding_for(plan.goal.goal_state)
    completion = binding.completion_for(plan.goal.parameters) if binding else ""
    # The model names the subject in its own words, so the control it resolves to
    # may not exist here. Say so and stop, rather than clicking into a timeout.
    if binding is None or not completion or not exists(session, completion):
        panel.conclude("Understood, but this page has no control for it.", "no")
        time.sleep(pace * 2)
        return None

    # What the model extracted is typed in before the button is pressed. Without
    # this the run would book whatever the form happened to be showing and still
    # report success, which is the same class of false success this demo exists
    # to catch - just committed by the runner instead of the page.
    filled = binding.bindings_for(plan.goal.parameters)
    for name, control in filled.items():
        if exists(session, control):
            session.fill(control, str(plan.goal.parameters[name]))
    if filled:
        entered = ", ".join(f"{n}={plan.goal.parameters[n]!r}" for n in filled)
        panel.conclude(f"entered from the model's own answer: {entered}", "ok")
        time.sleep(pace * 0.5)  # a short line, and the values are echoed on screen

    session.click(completion)
    return (
        binding.success_region(plan.goal.parameters),
        binding.success_for(plan.goal.parameters),
        binding.visual_question(plan.goal.parameters),
    )


def act_on_device(
    session: Any,
    panel: ModelPanel,
    plan: Any,
    room: Room,
    *,
    pace: float,
) -> tuple[str, str, str] | None:
    """Do a goal no control on the page can do: write to the device itself.

    The dashboard displays these properties but offers no way to change them, so
    the action leaves the browser entirely. Where to write is resolved from the
    Thing Descriptions the room published - nothing here names an endpoint - and
    the value is confirmed by reading the property back from the device before
    the page is consulted at all.
    """
    binding = device_binding_for(plan.goal.goal_state)
    view = device_view_for(plan.goal.goal_state)
    if binding is None or view is None:
        panel.conclude("Understood, but this room has no device for it.", "no")
        time.sleep(pace * 2)
        return None
    if not room.ready:
        panel.conclude(f"The Thing Directory is not answering: {room.error}", "no")
        time.sleep(pace * 2)
        return None

    resolved = resolve_device_target(binding, room.models, plan.goal.parameters)
    if isinstance(resolved, DeviceResolutionError):
        panel.conclude(f"Not attempted: {resolved.detail}", "no")
        time.sleep(pace * 2)
        return None

    where = f"{resolved.thing_title or resolved.thing_id}.{resolved.property}"
    panel.conclude(
        f"discovered {room.titles()}; resolved to {where} = {resolved.value} (from the Thing Descriptions)",
        "ok",
    )
    time.sleep(pace * 1.4)

    try:
        room.executor.write_state(resolved.source, resolved.value)
        observed = room.executor.read_state(resolved.source)
    except Exception as exc:  # a failed write is a result, not a crash
        panel.conclude(f"the write failed: {type(exc).__name__}: {exc}", "no")
        time.sleep(pace * 2)
        return None

    # The servient answers a write that changed nothing with a success status, so
    # the status is not the evidence. Reading the setpoint back says the device
    # was told - which is a smaller claim than the goal, and the one that is
    # available immediately.
    accepted = values_match(resolved.value, observed)
    panel.conclude(
        f"setpoint read back: {observed!r} " f"{'- the device was told' if accepted else '- NOT what was asked for'}",
        "ok" if accepted else "no",
    )
    time.sleep(pace * 1.2)

    # And then the part a page cannot have. The room has mass: the setpoint is a
    # fact at once and the temperature arrives later, or does not arrive at all if
    # something physical is wrong. A goal about the room is only met by the second
    # reading, so the run waits for it and says how long it took.
    if resolved.measured_source is not None:
        reached, took, last = await_measurement(room, resolved, timeout=8.0)
        if reached:
            panel.conclude(
                f"the room reached it: {resolved.measured_property}={last!r} after {took:.1f}s "
                f"(the setpoint was true {took:.1f}s earlier)",
                "ok",
            )
        else:
            panel.conclude(
                f"the setpoint holds and the room did not follow: "
                f"{resolved.measured_property}={last!r} after {took:.1f}s",
                "no",
            )
        time.sleep(pace * 1.4)

    # The dashboard polls the devices on its own schedule, so the digital half
    # lags the physical one. Waiting for the value to appear rather than sleeping
    # a fixed guess is both quicker in the normal case and still correct on the
    # run where the poll happens to have just gone out.
    proof = view.proof_for(resolved.value)
    await_text(session, view.value_selector, proof, timeout=4.0)
    return view.region, proof, view.question_for(resolved.value)


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
    room: Room,
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

    # --- act ------------------------------------------------------------------
    # Two surfaces, one goal vocabulary. A goal the dashboard has a control for is
    # done by using that control; a goal about a device is done over WoT, because
    # the dashboard shows those properties and offers no way to change them.
    if device_binding_for(plan.goal.goal_state) is not None:
        record.surface = "device (WoT)"
        acted = act_on_device(session, panel, plan, room, pace=pace)
    else:
        record.surface = "dashboard (DOM)"
        acted = act_on_page(session, panel, plan, pace=pace)
    if acted is None:
        return record
    region, proof, question = acted

    # --- then check what actually happened, twice -----------------------------
    if scene.fault:
        FAULTS[scene.fault].apply(session, region)
        panel.conclude(f"fault injected: {FAULTS[scene.fault].name}", "no")
        time.sleep(pace * 1.6)

    observed = (session.text_content(region) or "").lower()
    record.dom_says_met = bool(proof) and proof.lower() in observed
    panel.show_oracle(
        f"{proof!r} in {region}: {'found - goal reached' if record.dom_says_met else 'not found'}",
        record.dom_says_met,
    )
    time.sleep(pace * 1.6)

    # --- the second modality ---------------------------------------------------
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
    # The verdict is one line and the panel stays up while the next scene loads,
    # so this is the beat to shorten when the run needs room. The beats that
    # carry new information - the fault, the read-back, the model's own words -
    # keep their timing.
    time.sleep(pace * 1.9)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pace", type=float, default=1.5, help="Seconds per beat.")
    parser.add_argument("--type-delay", type=float, default=0.12, help="Seconds per revealed line of a reply.")
    parser.add_argument("--hold", type=float, default=3.0, help="Seconds to stay on the final summary.")
    parser.add_argument("--headless", dest="headed", action="store_false", default=True)
    parser.add_argument("--record", action="store_true", help="Capture the page and convert it to mp4.")
    parser.add_argument("--dashboard", default=DASHBOARD_URL, help="Smart-room dashboard URL.")
    parser.add_argument("--directory", default=DIRECTORY_URL, help="Thing Directory base URL.")
    parser.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        default=True,
        help="Keep whatever the last run left in the room instead of resetting first.",
    )
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

    # The declared use case: the dashboard is the page a person uses, and the
    # Things behind it are reached over WoT instead of by clicking. Both are
    # served by docker compose, so this run needs the room to be up.
    url = args.dashboard
    if not reachable(url):
        print(f"\n  the smart-room dashboard is not answering at {url}")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2

    # Without this the device scene stops proving anything on the second run: the
    # previous run left the thermostat at 22, so a write that never arrived still
    # reads back 22 and a broken write looks confirmed. A rehearsal followed by a
    # live run is exactly that sequence, which makes this a demo-day problem
    # rather than a theoretical one.
    reset_error = reset_room() if args.reset else "skipped by --no-reset"

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

    room = discover_room(args.directory)

    print(f"\n{_LINE}\n  THE SAME LOOP, WITH AND WITHOUT A MODEL\n{_LINE}")
    print(f"  surface      : {url}   (the dashboard: the digital half)")
    print(f"  devices      : {room.titles()}   (from {args.directory}/things: the WoT surface)")
    print(f"  room reset   : {'yes, to its initial state' if not reset_error else reset_error}")
    if room.ready and room.error:
        print(f"  note         : the TDs advertise an address the host cannot reach; rewrote {room.error}")
    elif not room.ready:
        print(f"  note         : the directory is not answering ({room.error}); the device scene will decline")
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
                room=room,
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
