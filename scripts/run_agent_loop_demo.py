"""The agent driving several environments, narrated inside one browser window.

    python scripts/run_agent_loop_demo.py

Built to be understood by someone who has never seen the project. A side panel
says, for every step: which phase of the loop it is, what is happening in plain
language, why the step exists, and the source that is running. The page itself
shows a cursor moving to the element and a highlight where the agent acts, so
the narration and the action are visible in the same frame.

Seven scenes across three surfaces - a shop, a forum and a WoT device - six of
them with a fault injected on purpose. Every fault is a different class taken
from things that break real automation (layout shift, consent banner, disabled
control, optimistic rollback, expired session, silent device write), ordered
easy to hard, so the run shows failures being diagnosed and answered differently
rather than one rehearsed recovery repeated.

The counts the metrics are computed from run along the bottom of the panel at
every step, faulted or not, so the final figures can be checked against
something the viewer watched accumulate.

Deliberately one browser window. An earlier version opened a separate
Picture-in-Picture window and ran each scene in its own subprocess, so a
recording had two windows to follow and the screen flickered between scenes.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.demos.ledger import MetricLedger  # noqa: E402
from src.demos.narration_console import AgentConsole  # noqa: E402
from src.demos.probes import text_snapshot  # noqa: E402
from src.demos.realistic_faults import FAULTS  # noqa: E402
from src.perception.dom_transducer import DomTransducer  # noqa: E402
from src.perception.som_parser import marks_from_affordances  # noqa: E402

_LINE = "=" * 78

# Draws the agent's pointer and rings the element it is about to touch, so a
# viewer can follow the action on the page instead of only in the panel.
_POINT_JS = (
    "(a)=>{"
    "let c=document.getElementById('__cua_cur');"
    "if(!c){c=document.createElement('div');c.id='__cua_cur';"
    "c.style.cssText='position:fixed;width:26px;height:26px;z-index:2147483645;"
    "pointer-events:none;transition:left .55s cubic-bezier(.4,0,.2,1),top .55s cubic-bezier(.4,0,.2,1);"
    "background:conic-gradient(from 135deg at 30% 30%,#8383ff 0 25%,transparent 0);"
    "clip-path:polygon(0 0,0 78%,26% 58%,44% 96%,62% 86%,44% 50%,78% 46%);"
    "filter:drop-shadow(0 0 7px rgba(131,131,255,.95))';"
    "document.body.appendChild(c);}"
    "c.style.left=(a.x-3)+'px';c.style.top=(a.y-3)+'px';"
    "let r=document.getElementById('__cua_ring');"
    "if(!r){r=document.createElement('div');r.id='__cua_ring';"
    "r.style.cssText='position:fixed;z-index:2147483644;pointer-events:none;"
    "border:3px solid #8383ff;border-radius:8px;transition:all .5s;"
    "box-shadow:0 0 22px rgba(131,131,255,.85),inset 0 0 22px rgba(131,131,255,.3)';"
    "document.body.appendChild(r);}"
    "r.style.left=a.bx+'px';r.style.top=a.by+'px';"
    "r.style.width=a.bw+'px';r.style.height=a.bh+'px';"
    "r.style.borderColor=a.color;r.style.opacity='1';return true;}"
)

_CLEAR_POINT_JS = (
    "()=>{for(const i of ['__cua_cur','__cua_ring']){"
    "const e=document.getElementById(i);if(e)e.remove();}return true;}"
)


@dataclass
class StepRecord:
    phase: str
    detail: str
    ok: bool = True


@dataclass
class Trajectory:
    goal: str
    steps: list[StepRecord] = field(default_factory=list)
    recovered: bool = False
    intent: dict[str, Any] = field(default_factory=dict)  # layer 1: utterance -> goal
    decision: dict[str, Any] = field(default_factory=dict)  # layer 3: which mark, and why
    observation: dict[str, Any] = field(default_factory=dict)  # what the probes measured after a failure
    diagnosis: dict[str, Any] = field(default_factory=dict)  # why it failed, and the tier chosen
    fault_kind: str = ""
    escalated: bool = False
    goal_met: bool = False

    def add(self, phase: str, detail: str, ok: bool = True) -> None:
        self.steps.append(StepRecord(phase, detail, ok))
        print(f"  [{'ok ' if ok else 'FAIL'}] {phase:<9} {detail}")

    def failures(self) -> list[StepRecord]:
        return [s for s in self.steps if not s.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "recovered": self.recovered,
            "intent": self.intent,
            "decision": self.decision,
            "observation": self.observation,
            "diagnosis": self.diagnosis,
            "fault_kind": self.fault_kind,
            "escalated": self.escalated,
            "steps": [{"phase": s.phase, "detail": s.detail, "ok": s.ok} for s in self.steps],
        }


# --- the loop, one function per phase so the panel can show its source --------


def observe(session: Any) -> Any:
    """Transduce the live DOM into a Page Affordance Model.

    Selectors are derived from the page and ranked by how stable they are:
    id > data-testid > name > class > position.
    """
    return DomTransducer().transduce(session.content_html(), page_id="loop_demo")


def measure(session: Any, pam: Any) -> int:
    """Ask the live browser for each affordance's real bounding box.

    Nothing comes from fixtures: an element that cannot be measured gets no
    box, so a visual mark only ever describes geometry we observed.
    """
    from src.perception.visual_geometry import attach_measured_bboxes

    return attach_measured_bboxes(pam, session)


def interpret(intent: str) -> Any:
    """Layer 1: turn what a person said into a structured goal.

    A run starts from an utterance, not from a goal someone wrote by hand. The
    result records whether a model interpreted it or the deterministic fallback
    matched a phrasing, so the difference is never guessed at.
    """
    from src.planner.intent_planner import IntentPlanner, available_client

    return IntentPlanner(client=available_client()).plan(intent)


def choose_target(pam: Any, goal: str) -> Any:
    """Layer 3: pick which Set-of-Marks target advances the goal.

    This is what the numbering exists for: a model answers with an identifier
    like "M002" rather than guessing pixel coordinates. With no model
    configured the deterministic scorer answers instead, and the result says
    which of the two it was.
    """
    from src.planner.intent_planner import available_client
    from src.planner.mark_selector import MarkSelector

    return MarkSelector(client=available_client()).select(marks_from_affordances(pam.affordances), goal)


def act(session: Any, selection: Any) -> None:
    """Click the centre of the chosen mark.

    Driven by the visual mark, not by a CSS selector, which is what makes this
    the Set-of-Marks path rather than ordinary DOM automation.
    """
    session.click_xy(*selection.bbox.center)


def verify(session: Any, where: str, expect: str) -> bool:
    """Re-read the page and check the effect the goal actually named.

    Executor success is not task success, and neither is "something changed".
    An earlier version searched the whole page for the word "added", which
    passed when a mistargeted click added the wrong product.

    Two things this has to survive. An optimistic interface confirms before the
    server agrees, so reading immediately records a state that may not last -
    hence the settle. And the region can be gone entirely, which is a failure to
    report rather than an error to raise.
    """
    time.sleep(0.6)  # let an optimistic update be confirmed or reverted
    try:
        text = session.evaluate("(s)=>{const e=document.querySelector(s);return e?e.innerText:null;}", where)
    except Exception:
        return False
    return text is not None and expect.lower() in str(text).lower()


def inspect_failure(session: Any, attempted: Any, region: str, text_before: str) -> Any:
    """Put four specific questions to the live page about the failed step.

    Comparing a label and two coordinates can only tell "it moved" from "it is
    gone". It cannot see a banner sitting on the button, a control that refuses
    input, or a click that landed somewhere else - so each of those is measured
    directly, at the point the agent actually clicked.
    """
    from src.demos.probes import Observation, hit_test, interactability, occlusion, text_snapshot

    selector = str(attempted.extra.get("selector", ""))
    x, y = attempted.bbox.center
    return Observation(
        hit=hit_test(session, x, y, selector),
        interact=interactability(session, selector),
        occlusion=occlusion(session, selector),
        text_before=text_before,
        text_after=text_snapshot(session, region),
    )


def diagnose_failure(session: Any, attempted: Any, goal: str, observation: Any) -> Any:
    """Turn the measurements into a conclusion and a recovery tier.

    Nothing here is told which fault was injected. The probes say what the page
    is like now; re-observing says whether the element is still there and still
    where it was; those answers alone pick the tier.
    """
    from src.demos.diagnosis import diagnose_with_probes

    fresh_pam = observe(session)
    measure(session, fresh_pam)
    same = [m for m in marks_from_affordances(fresh_pam.affordances) if m.label == attempted.label]
    other = choose_target(fresh_pam, goal) if not same else None

    return diagnose_with_probes(
        observation,
        moved=bool(same) and same[0].bbox.center != attempted.bbox.center,
        still_present=bool(same),
        alternative_label=other.mark.label if other is not None and other.ok else "",
    )


def clear_obstruction(session: Any, observation: Any, attempted: Any) -> bool:
    """Tier 2 for an occlusion: act on whatever intercepted the click.

    The obstruction is never named in code. The probe returned the rectangle of
    the element that received the click, so the agent re-observes and takes the
    one control inside that rectangle - a banner's own dismiss button, in
    practice - rather than being handed a selector for it.
    """
    left, top, width, height = observation.occlusion.coverer_rect
    fresh = observe(session)
    measure(session, fresh)
    inside = [
        m
        for m in marks_from_affordances(fresh.affordances)
        if m.label != attempted.label
        and left <= m.bbox.center[0] <= left + width
        and top <= m.bbox.center[1] <= top + height
    ]
    if not inside:
        return False
    session.click_xy(*inside[0].bbox.center)
    return True


def satisfy_precondition(session: Any, attempted: Any) -> bool:
    """Tier 3 for a refused control: meet what it is waiting on, then check.

    A disabled control is not broken, so the answer is to satisfy its
    precondition rather than to keep clicking. The control declares what it
    depends on through aria-controls, which is how an accessible form states it
    - the agent reads that, fills the field, and only then re-measures. If the
    control still refuses input, it does not act.
    """
    from src.demos.probes import interactability

    selector = str(attempted.extra.get("selector", ""))
    required = session.evaluate(
        "(s)=>{const e=document.querySelector(s);return e?(e.getAttribute('aria-controls')||''):'';}", selector
    )
    if not required:
        return False
    session.fill(f"#{required}", "1")
    return interactability(session, selector).actionable


def apply_recovery(session: Any, diagnosis: Any, goal: str, observation: Any, attempted: Any) -> bool:
    """Carry out the tier the diagnosis selected.

    Four genuinely different responses, not one retry wearing four labels:
    tier 1 looks again and repeats, tier 2 deals with the obstruction first,
    tier 3 satisfies the precondition and refuses to act if it cannot,
    tier 4 does not act at all.
    """
    from src.demos.diagnosis import STRATEGY_CLEAR, STRATEGY_ESCALATE, STRATEGY_ROLLBACK

    if diagnosis.strategy == STRATEGY_ESCALATE:
        return False  # deliberately does not act; the handover is the response
    if diagnosis.strategy == STRATEGY_CLEAR and not clear_obstruction(session, observation, attempted):
        return False
    if diagnosis.strategy == STRATEGY_ROLLBACK and not satisfy_precondition(session, attempted):
        return False

    fresh = observe(session)
    measure(session, fresh)
    result = choose_target(fresh, goal)
    if not result.ok:
        return False
    act(session, result.mark)
    return True


def make_console_safe() -> None:
    """Stop a page's own text from being able to kill the run.

    The demo reports what it observed on the page, and the pages contain emoji
    (the shop renders `&#127911;` as a headphones glyph). On a console using a
    regional code page - GBK on a Chinese Windows install - encoding that
    character raises UnicodeEncodeError and takes the whole run down partway
    through, on a machine where nothing is actually wrong.

    The console keeps its own encoding; only the error policy changes, so an
    unrepresentable glyph becomes "?" instead of an exception. Losing a
    character from a log line is not worth losing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass  # not a reconfigurable stream; nothing to do


def to_mp4(out: Path) -> str:
    """Convert the recorded .webm to .mp4, if a converter can be found.

    Playwright only writes webm, which most players and every slide deck refuse.
    ffmpeg is used when it is on PATH; otherwise the copy bundled with
    imageio-ffmpeg is used, so this works without installing anything system
    wide. If neither is available the webm is left alone and the command to run
    later is printed - a demo run should not fail over a file format.
    """
    import shutil
    import subprocess

    videos = sorted(out.glob("*.webm"))
    if not videos:
        return ""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            print(f"  [note] no ffmpeg; convert later with:  ffmpeg -i {videos[0].name} agent_loop_demo.mp4")
            return ""

    target = out / "agent_loop_demo.mp4"
    command = [ffmpeg, "-y", "-i", str(videos[0]), "-c:v", "libx264", "-preset", "medium"]
    command += ["-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except Exception as exc:  # a failed conversion must not fail the run
        print(f"  [note] mp4 conversion failed ({exc}); the webm is still there")
        return ""
    return target.name


def escalate(reason: str) -> Any:
    """Tier 4: pause, hand to a supervisor, and record what they did.

    An unattended demo cannot wait for a person, so the handover is recorded as
    approved-without-change. That still exercises the real path, and the
    correction rate stays honest about it.
    """
    from src.recovery.supervised_takeover import SupervisedTakeover

    takeover = SupervisedTakeover()
    takeover.pause("loop-demo", reason)
    takeover.resume()  # approved, nothing changed
    return takeover.metrics()


# --- scenes -------------------------------------------------------------------


@dataclass
class Scene:
    page: str
    title: str
    goal_text: str
    utterance: str
    target: str  # label fragment the planner looks for
    check_in: str  # selector whose text proves the effect
    expect: str  # text that must appear there
    fault: str = ""  # key into realistic_faults.FAULTS, or a scene-local fault name
    fault_selector: str = ""  # the element the fault acts on
    # Ground truth for scenes whose fault is not one of the page faults - the
    # smart room injects a device-level one of its own.
    scored_as: tuple[str, int] = ("", 0)

    @property
    def injected(self) -> Any:
        """The page fault to apply, or None if this scene does not use one."""
        return FAULTS.get(self.fault)

    @property
    def expected_cause(self) -> str:
        """Ground truth for scoring only. Never reaches the diagnosis."""
        return self.scored_as[0] or (self.injected.expected_cause if self.injected else "")

    @property
    def expected_tier(self) -> int:
        """The tier this fault warrants, held here only so the run can be scored."""
        return self.scored_as[1] or (self.injected.expected_tier if self.injected else 0)


# Seven scenes, six of them faulted, ordered easy to hard. Every fault is a
# different class drawn from things that break real automation, so the agent
# cannot answer them all the same way: one is recoverable by looking again, one
# needs the obstruction dealt with, one needs a precondition satisfied, and two
# cannot be recovered at all and have to be handed over. Which tier each ends up
# using is decided at run time from what the agent measures; it is not written
# down here, and the recovery code is never told which fault was used.
SCENES = [
    Scene(
        page="shopping.html",
        title="Online shop - content loads above the target and shifts it",
        goal_text="Add the Wireless Headphones to the cart",
        utterance="Could you put the wireless headphones in my cart?",
        target="Headphones",
        check_in="#cart-items",
        expect="Wireless Headphones",
        fault="layout_shift",
        fault_selector="button.add-cart-btn[data-id='headphones']",
    ),
    Scene(
        page="forum.html",
        title="Discussion forum - a consent banner lands on the target",
        goal_text="Upvote the top-ranked post",
        utterance="Upvote the top post.",
        target="AI agents",
        check_in="#votes-1",
        expect="43",
        fault="consent_overlay",
        fault_selector="button.upvote-btn[data-post='1']",
    ),
    Scene(
        page="shopping.html",
        title="Online shop - the control refuses input until a field is filled",
        goal_text="Add the Pro Laptop to the cart",
        utterance="Add the pro laptop to my cart please.",
        target="Laptop",
        check_in="#cart-items",
        expect="Pro Laptop",
        fault="disabled_until_valid",
        fault_selector="button.add-cart-btn[data-id='laptop']",
    ),
    Scene(
        page="shopping.html",
        title="Online shop - the interface confirms, then the server rejects",
        goal_text="Add the Mechanical Keyboard to the cart",
        utterance="Add the mechanical keyboard to my cart.",
        target="Keyboard",
        check_in="#cart-items",
        expect="Mechanical Keyboard",
        fault="optimistic_rollback",
        fault_selector="button.add-cart-btn[data-id='keyboard']",
    ),
    Scene(
        page="forum.html",
        title="Discussion forum - the session expires between planning and acting",
        goal_text="Upvote the browser-automation post",
        utterance="Give the browser automation post an upvote.",
        target="browser automation",
        check_in="#votes-2",
        expect="29",
        fault="session_expiry",
        fault_selector="button.upvote-btn[data-post='2']",
    ),
    Scene(
        page="shopping.html",
        title="Online shop - clean run, nothing injected",
        goal_text="Add the 4K Monitor to the cart",
        utterance="Add the 4k monitor to my cart.",
        target="Monitor",
        check_in="#cart-items",
        expect="4K Monitor",
    ),
    # The third surface: a device driven through its Thing Description, not a
    # page. Same loop, same verification rule, different world.
    Scene(
        page="smart_room.html",
        title="Smart room - the device accepts the write and ignores it",
        goal_text="Set the thermostat to 22 degrees",
        utterance="Set the thermostat to 22 degrees.",
        target="targetTemperature",
        check_in="#target",
        expect="22",
        fault="silent_write",  # device-level, applied by the smart-room scene itself
        fault_selector="",
        scored_as=("action_had_no_effect", 4),
    ),
]


def point_at(session: Any, console: AgentConsole, selection: Any, colour: str) -> None:
    """Move the pointer and ring onto the mark the agent is about to use."""
    box = selection.bbox  # BoundingBox: x/y/w/h, with .center
    session.evaluate(
        _POINT_JS,
        {
            "x": box.center[0],
            "y": box.center[1],
            "bx": box.x,
            "by": box.y,
            "bw": box.w,
            "bh": box.h,
            "color": colour,
        },
    )


def run_scene(
    session: Any,
    console: AgentConsole,
    scene: Scene,
    *,
    pace: float,
    trace_delay: float,
    ledger: MetricLedger,
    familiar: bool = False,
) -> Trajectory:
    # The loop is the same every scene; the fault, the diagnosis and the
    # recovery are not. Once a viewer has been walked through observe/measure/
    # decide/act/verify, repeating those explanations at full length seven times
    # is the single biggest cost in the run and teaches nothing new - so after
    # the first scene the familiar beats are shortened and the interesting ones
    # keep their timing. Nothing is skipped; every step is still narrated.
    teach = pace * 0.22 if familiar else pace
    # The fault, diagnosis and recovery beats differ every scene, so they keep
    # their shape - but a viewer who has watched one recovery reads the next one
    # faster, so they are tightened rather than left at first-scene length. The
    # floor is what keeps them readable: these are the beats carrying the new
    # information, and shrinking them with the pace would defeat the point.
    beat = max(pace * 0.45, 0.6) if familiar else pace
    # Same reasoning for the line highlight: watching the interpreter walk
    # observe() is worth its full speed once. By the sixth scene it is scenery,
    # and it is the single largest fixed cost in the run.
    trace = trace_delay * 0.35 if familiar else trace_delay
    traj = Trajectory(goal=scene.goal_text)
    console.open(f"{scene.title}  |  said: “{scene.utterance}”")

    def tally() -> None:
        """Refresh the running counts the metrics are computed from.

        Called after every step, faulted or not, so nothing in the final table
        appears without having been watched accumulate.
        """
        console.tally(ledger.counters.as_strip())

    tally()
    console.banner(f"SCENE: {scene.title}", "#4f46e5")
    time.sleep(teach * 0.8)
    console.hide_banner()

    # Layer 1. A run starts from an utterance, not from a goal written by hand.
    console.step(
        "0/5",
        "INTERPRET",
        f"Someone said: “{scene.utterance}”",
        "Turning a sentence into a structured goal the runtime can act on. The "
        "result records whether a model interpreted it or a phrasing rule matched, "
        "because a fallback that reads as understanding would make the whole claim "
        "unverifiable.",
        interpret,
    )
    time.sleep(teach)
    goal_plan = console.run_traced(interpret, scene.utterance, line_delay=trace)
    traj.intent = goal_plan.to_dict()
    understood = goal_plan.ok
    traj.add(
        "interpret",
        f"{goal_plan.source}: {goal_plan.goal.goal_state if goal_plan.ok else 'not understood'}",
        understood,
    )
    console.step(
        "0/5",
        f"INTERPRET - {goal_plan.source}",
        (
            f"Understood as: {goal_plan.goal.goal_state}"
            if understood
            else "Could not turn this sentence into a supported goal."
        ),
        (
            "A model interpreted this."
            if goal_plan.is_model_derived
            else "No model is configured, so a phrasing rule matched instead. Labelled as "
            "such rather than presented as understanding."
        ),
        json.dumps(goal_plan.to_dict(), indent=2),
    )
    console.result(
        "interpret",
        f"{goal_plan.source} (confidence {goal_plan.confidence:.2f})",
        understood,
        f"understood as {goal_plan.goal.goal_state}" if understood else "did not understand the request",
    )
    time.sleep(teach * 1.4)

    console.step(
        "1/5",
        "OBSERVE",
        "Reading the page the way the agent sees it.",
        "The agent never hard-codes what is on screen. It re-derives every clickable "
        "thing from the live page, so the same code works on a page it has not seen.",
        "",
    )
    # Executed under the tracer, so the highlight follows the interpreter's own
    # path through the function rather than an animation of it.
    pam = console.run_traced(observe, session, line_delay=trace)
    ledger.observed(elements=len(pam.affordances))
    tally()
    traj.add("observe", f"{len(pam.affordances)} affordances perceived")
    console.result(
        "observe",
        f"{len(pam.affordances)} interactive elements found",
        True,
        f"found {len(pam.affordances)} things it could act on",
    )
    time.sleep(teach * 0.6)

    console.step(
        "2/5",
        "MEASURE",
        "Asking the browser where each element actually is.",
        "Positions are measured live, never taken from a fixture. Anything that cannot "
        "be measured gets no mark at all, so the agent cannot aim at something imagined.",
        measure,
    )
    time.sleep(teach)
    n = console.run_traced(measure, session, pam, line_delay=trace)
    ledger.measured(boxes=n)
    tally()
    traj.add("measure", f"{n} boxes measured")
    console.result("measure", f"{n} real screen positions", True, f"measured {n} on-screen positions")
    time.sleep(teach * 0.6)

    console.step(
        "3/5",
        "DECIDE",
        f"Choosing which of the {len(pam.affordances)} elements advances the goal.",
        "This is what numbering the elements is for: the answer is an identifier "
        "like M002, not a pixel coordinate. A model answers when one is configured; "
        "otherwise deterministic scoring does, and the result says which.",
        choose_target,
    )
    time.sleep(teach)
    selection_result = console.run_traced(choose_target, pam, scene.target, line_delay=trace)
    ledger.scored(candidates=selection_result.considered)
    tally()
    traj.decision = selection_result.to_dict()

    # Show the deliberation, not only its outcome. Both paths explain themselves
    # in the same field, so the panel reads the same either way.
    provenance = "a language model" if selection_result.is_model_derived else "deterministic scoring"
    console.step(
        "3/5",
        f"DECIDE - {selection_result.source}",
        f"{selection_result.considered} candidates were considered; the choice came from {provenance}.",
        "Whichever path answered, it has to say why. A model gives its reason; the "
        "scorer gives the ranking and the margin. Neither is allowed to be silent.",
        f"chosen: {selection_result.mark_id}\nconfidence: {selection_result.confidence:.2f}\n\n"
        f"{selection_result.reason}",
    )
    time.sleep(teach * 1.9)

    if not selection_result.ok:
        traj.add("decide", f"none of {selection_result.considered} candidates qualified", False)
        console.result("decide", selection_result.reason[:60], False, "could not find a way to do this")
        time.sleep(teach)
        return traj

    selection = selection_result.mark
    traj.add("decide", f"{selection_result.mark_id} via {selection_result.source}")
    console.result(
        "decide",
        f"{selection_result.mark_id} ({selection_result.source})",
        True,
        f"chose {selection_result.mark_id} of {selection_result.considered}",
    )
    point_at(session, console, selection, "#8383ff")
    time.sleep(teach)

    fault = scene.injected
    if fault is not None:
        console.step(
            "!",
            f"FAULT: {fault.name}  ({fault.difficulty})",
            fault.blurb(),
            f"What the agent will see: {fault.symptom}. Which fault this is never reaches "
            "the recovery code - it has to be worked out from what can be measured "
            "afterwards, which is the whole point of injecting a different one each time.",
            fault.apply,
        )
        time.sleep(beat * 0.5)
        injected = fault.apply(session, scene.fault_selector)
        traj.fault_kind = fault.key
        traj.add("fault", f"{fault.name} applied to the target" if injected else "injection missed")
        console.result("fault", fault.symptom, False, f"injected: {fault.name}")
        console.banner(f"{fault.name} - a real cause, not a contrived one. Watch what it does.", "#b91c1c")
        time.sleep(beat * 1.8)
        console.hide_banner()

    # The region the goal names, as it stands before acting. Comparing this
    # exact region afterwards is what catches a change that undoes itself.
    region_before = text_snapshot(session, scene.check_in)

    console.step(
        "4/5",
        "ACT",
        "Clicking the centre of the chosen mark.",
        "The click is aimed at the visual mark, not at a CSS selector. That is what makes "
        "this the same code path a vision-driven agent would use.",
        act,
    )
    time.sleep(teach)
    act(session, selection)
    ledger.acted()
    tally()
    traj.add("act", f"clicked {selection.bbox.center}")
    console.result("act", f"clicked at {selection.bbox.center}", True, "click sent")
    time.sleep(teach * 0.8)

    console.step(
        "5/5",
        "VERIFY",
        "Re-reading the page to check the goal was really met.",
        "The click reporting success is not proof. The agent re-reads the page and looks "
        "for the exact outcome the goal named.",
        verify,
    )
    time.sleep(teach)
    ok = console.run_traced(verify, session, scene.check_in, scene.expect, line_delay=trace)
    ledger.verified(passed=ok)
    tally()
    traj.add("verify", "goal state confirmed" if ok else "expected effect NOT observed", ok)
    console.result(
        "verify",
        f"'{scene.expect}' present" if ok else f"'{scene.expect}' NOT found",
        ok,
        "goal confirmed" if ok else "the goal was NOT met",
    )
    time.sleep(teach)

    if not ok:
        console.banner("Failure detected by the agent itself. Measuring before deciding.", "#b45309")
        time.sleep(teach)
        console.hide_banner()

        # 6a. Measure first. Everything after this rests on what these four
        # probes return, and each of them reports "could not run" rather than a
        # default, so a conclusion can never be built on an absent measurement.
        console.step(
            "6/8",
            "INSPECT",
            "Asking the page four specific questions about the failed step.",
            "A diagnosis that only compares coordinates cannot tell a covered button from "
            "a disabled one from a dead one. Each is a different failure with a different "
            "answer, so each is measured directly rather than inferred.",
            inspect_failure,
        )
        observation = console.run_traced(
            inspect_failure, session, selection, scene.check_in, region_before, line_delay=trace
        )
        ledger.probed(len(observation.evidence()))
        tally()
        traj.observation = observation.to_dict()
        for line in observation.evidence():
            traj.add("inspect", line)
        console.step(
            "6/8",
            "INSPECT - measurements",
            "What the page reported, before any conclusion was drawn from it.",
            "These are measurements, not verdicts. The reasoning that combines them is "
            "the next step, and it is kept separate so it can be read and disagreed with.",
            "\n".join(f"- {line}" for line in observation.evidence()),
        )
        console.result("inspect", f"{len(observation.evidence())} measurements taken", True, "measured the failure")
        time.sleep(beat * 1.6)

        # 6b. Work out why, from those measurements alone.
        console.step(
            "7/8",
            "DIAGNOSE",
            "Turning the measurements into a conclusion and a recovery tier.",
            "Nothing tells this code which fault was injected. The probes say what the page "
            "is like now; re-observing says whether the element is still there and still "
            "where it was. Those answers alone pick the tier.",
            diagnose_failure,
        )
        diagnosis = console.run_traced(
            diagnose_failure, session, selection, scene.target, observation, line_delay=trace
        )
        ledger.diagnosed(diagnosis.cause, diagnosis.tier)
        tally()
        traj.diagnosis = diagnosis.to_dict()
        traj.add("diagnose", f"{diagnosis.cause} -> tier {diagnosis.tier} ({diagnosis.strategy})")

        console.step(
            "7/8",
            f"DIAGNOSE - {diagnosis.cause}",
            f"Concluded: {diagnosis.cause}. Chosen response: tier {diagnosis.tier}.",
            diagnosis.reasoning,
            diagnosis.explain(),
        )
        console.result(
            "diagnose",
            f"tier {diagnosis.tier}: {diagnosis.strategy}",
            True,
            f"diagnosed: {diagnosis.cause}",
        )
        time.sleep(beat * 2.2)

        # 6c. Carry out whatever the diagnosis chose.
        console.step(
            "8/8",
            f"RECOVER - tier {diagnosis.tier}",
            f"Applying: {diagnosis.strategy.replace('_', ' ')}.",
            "Tier 1 looks again and repeats. Tier 2 deals with the obstruction first. "
            "Tier 3 waits for the precondition and refuses to act if it never holds. "
            "Tier 4 deliberately does not act at all: repeating something that provably "
            "does nothing would only waste the attempt.",
            apply_recovery,
        )
        acted = console.run_traced(
            apply_recovery, session, diagnosis, scene.target, observation, selection, line_delay=trace
        )
        time.sleep(beat * 0.6)

        if diagnosis.tier >= 4:
            metrics = escalate(f"{diagnosis.cause}: {diagnosis.strategy}")
            ledger.escalated()
            tally()
            traj.escalated = True
            traj.add("escalate", f"handed to a supervisor; correction rate {metrics['correction_rate']:.2f}")
            console.result("escalate", "paused and handed over", True, "escalated to a human, as designed")
            console.banner("Correctly refused to retry. Escalated to a human.", "#b45309")
            time.sleep(beat * 1.8)
            console.hide_banner()
        else:
            if acted:
                ledger.recovered()
            tally()
            traj.add("recover", f"tier {diagnosis.tier} applied", acted)
            ok = console.run_traced(verify, session, scene.check_in, scene.expect, line_delay=trace)
            ledger.verified(passed=ok)
            tally()
            traj.recovered = ok
            traj.add("verify", "confirmed after recovery" if ok else "still failing", ok)
            console.result(
                "verify",
                "confirmed after recovery" if ok else "still failing",
                ok,
                "recovered on its own" if ok else "could not recover",
            )
            console.banner(
                "Recovered without human help." if ok else "Recovery failed.",
                "#15803d" if ok else "#b91c1c",
            )
            time.sleep(beat * 1.6)
            console.hide_banner()

    ledger.episode_done(goal_met=ok)
    tally()
    traj.goal_met = ok
    session.evaluate(_CLEAR_POINT_JS)
    return traj


def run_wot_scene(
    session: Any,
    console: AgentConsole,
    *,
    pace: float,
    trace_delay: float,
    faulty: bool,
    ledger: MetricLedger,
    familiar: bool = False,
) -> Trajectory:
    """Drive a device through its Thing Description, on the same loop.

    The project claims one action system across DOM, WoT and visual surfaces,
    but the loop demo only ever showed two of them. This is the third: property
    endpoints parsed from the project's own thermostat TD, read and written over
    the forms the description declares.

    The fault here is the one the review named - the write is acknowledged and
    the device state does not follow - and it is invisible in the response. Only
    reading the property back can catch it, which is the whole argument for
    verifying independently of the executor.
    """
    from src.demos.wot_scene import (
        FakeServient,
        load_thermostat_td,
        perceive_device,
        read_property,
        verify_device,
        write_property,
    )

    # Always the last scene, so the viewer has watched the loop six times by
    # now: the beats are tightened for the same reason as in run_scene.
    beat = max(pace * 0.45, 0.6) if familiar else pace
    trace = trace_delay * 0.35 if familiar else trace_delay
    traj = Trajectory(goal="Set the thermostat to 22 degrees")
    servient = FakeServient({"targetTemperature": 18, "currentTemperature": 18})
    td = load_thermostat_td()

    def show() -> None:
        session.evaluate(
            "(s)=>window.__setDevice&&window.__setDevice(s)",
            {
                "target": servient.state["targetTemperature"],
                "current": servient.state["currentTemperature"],
                "stale": faulty,
            },
        )
        recent = servient.calls[-3:]
        session.evaluate(
            "(t)=>window.__setExchange&&window.__setExchange(t)",
            "\n".join(f"{m:<5} {p:<20} {v if v is not None else ''}" for m, p, v in recent) or "no request yet",
        )

    title = "Smart room - the device accepts the write and ignores it" if faulty else "Smart room - clean device write"
    console.open(f"{title}  |  said: “Set the thermostat to 22 degrees.”")
    console.banner(f"SCENE: {title}", "#4f46e5")
    time.sleep(beat * 0.8)
    console.hide_banner()
    show()

    console.step(
        "1/4",
        "PERCEIVE DEVICE",
        "Parsing the Thing Description into property endpoints.",
        "The device publishes what it can do, including which properties are writable "
        "and which are sensors. Nothing about this thermostat is written into the agent.",
        perceive_device,
    )
    sources = console.run_traced(perceive_device, td, line_delay=trace)
    writable = next(s for s in sources if not s.read_only)
    # A device property is the same kind of thing as a page affordance here: it
    # is something the agent perceived and could act on, so it counts the same.
    ledger.observed(elements=len(sources))
    ledger.measured(boxes=len(sources))
    ledger.scored(candidates=len(sources))
    console.tally(ledger.counters.as_strip())
    traj.add("perceive", f"{len(sources)} properties from the TD; '{writable.property}' is writable")
    console.result("perceive", f"{len(sources)} properties parsed", True, f"{len(sources)} device properties")
    time.sleep(beat * 0.7)

    console.step(
        "2/4",
        "READ",
        "Reading the current value through the form the TD declares.",
        "The starting state has to be observed, not assumed, or there is nothing to "
        "compare the result against afterwards.",
        read_property,
    )
    before = console.run_traced(read_property, servient.send, writable, line_delay=trace)
    traj.add("read", f"{writable.property} = {before}")
    console.result("read", f"{writable.property} = {before}", True, f"device reads {before}")
    time.sleep(beat * 0.7)

    if faulty:
        servient.silent_failure = True
        console.step(
            "!",
            "FAULT: silent write",
            "The device will accept the next write and leave its state unchanged.",
            "This is the failure mode the review singled out. The response is a normal "
            "success; nothing in it reveals the problem.",
            "servient.silent_failure = True   # answers 204, stores nothing",
        )
        traj.fault_kind = "silent_write"
        console.result("fault", "device will acknowledge and ignore", False, "fault injected: silent write")
        console.banner("The device will report success and do nothing.", "#b91c1c")
        time.sleep(beat * 1.4)
        console.hide_banner()

    console.step(
        "3/4",
        "WRITE",
        "Asking the device to change to 22 degrees.",
        "Sent over the TD's own write form, honouring the security scheme and rate " "limit the description declares.",
        write_property,
    )
    console.run_traced(write_property, servient.send, writable, 22, line_delay=trace)
    ledger.acted()
    console.tally(ledger.counters.as_strip())
    show()
    traj.add("write", "device accepted the write (204)")
    console.result("write", "accepted, HTTP 204", True, "the device said yes")
    time.sleep(beat * 0.8)

    console.step(
        "4/4",
        "VERIFY",
        "Reading the property back to see whether it actually changed.",
        "A 204 is not evidence. The only way to tell an applied write from an ignored "
        "one is to observe the state again.",
        verify_device,
    )
    ok = console.run_traced(verify_device, servient.send, writable, 22, line_delay=trace)
    ledger.verified(passed=ok)
    console.tally(ledger.counters.as_strip())
    show()
    traj.add("verify", f"{writable.property} reads {servient.state['targetTemperature']}", ok)
    console.result(
        "verify",
        f"reads {servient.state['targetTemperature']}, wanted 22",
        ok,
        "device state confirmed" if ok else "the device did not change",
    )
    time.sleep(beat)

    if not ok:
        # Nothing moved and the endpoint is still there, so a second identical
        # write would be ignored identically. The honest response is to hand over.
        console.step(
            "5/5",
            "DIAGNOSE and ESCALATE",
            "The write was accepted, the state did not follow, and the endpoint is unchanged.",
            "Retrying would send the same request to the same endpoint and be ignored the "
            "same way. The agent stops and hands over instead of burning attempts.",
            escalate,
        )
        metrics = escalate("silent device write: acknowledged but not applied")
        ledger.probed(1)  # reading the property back is the measurement this rests on
        ledger.diagnosed("action_had_no_effect", 4)
        ledger.escalated()
        console.tally(ledger.counters.as_strip())
        traj.escalated = True
        traj.diagnosis = {"cause": "action_had_no_effect", "strategy": "escalate_to_human", "tier": 4}
        traj.add("escalate", f"handed over; correction rate {metrics['correction_rate']:.2f}")
        console.result("escalate", "paused and handed over", True, "escalated, as designed")
        console.banner("Caught a failure the response never revealed.", "#b45309")
        time.sleep(beat * 1.8)
        console.hide_banner()

    ledger.episode_done(goal_met=ok)
    console.tally(ledger.counters.as_strip())
    traj.goal_met = ok
    return traj


def episode_result(scene: Scene, traj: Trajectory) -> Any:
    """Score one run against the fault that was injected.

    The expected cause and tier come from the scene definition, which the
    diagnosis never sees. Scoring the agent's answer against something it could
    not read is what makes the accuracy figures mean anything.
    """
    from src.demos.campaign import EpisodeResult

    return EpisodeResult(
        scene=scene.title,
        fault=scene.fault,
        expected_cause=scene.expected_cause,
        expected_tier=scene.expected_tier,
        diagnosed_cause=str(traj.diagnosis.get("cause", "")),
        chosen_tier=int(traj.diagnosis.get("tier", 0) or 0),
        failure_detected=bool(traj.diagnosis),
        goal_met=traj.goal_met,
        escalated=traj.escalated,
    )


def compile_experience(traj: Trajectory) -> Any:
    """Turn a failed step into a reviewable proposal using src/adaptation/."""
    from src.adaptation.experience_compiler import ExperienceCompiler
    from src.adaptation.failure_boundary import FailureAnalysis, FailureBoundary

    failed = traj.failures()
    if not failed:
        return None

    @dataclass
    class _Event:
        task_id: str = "loop_demo:add_to_cart"
        skill_id: str = "add_product_to_cart"
        backend: str = "visual"
        status: str = "failed"
        latency_ms: float = 0.0
        attempt: int = 1
        recovery_tier: int | None = 1
        failure_reason: str | None = None
        postcondition_passed: bool | None = False
        details: dict[str, Any] | None = None
        episode_id: str = "loop-demo"
        transition_id: str = "t1"
        state_id_before: str = "before"
        state_id_after: str = "after"

    analysis = FailureAnalysis(
        boundary=FailureBoundary.RECOVERABLE_EXECUTION_FAILURE,
        failure_type="stale_geometry_after_dom_mutation",
        evidence=[f"{s.phase}: {s.detail}" for s in failed],
        immediate_action="re-observe and re-plan before retrying",
        long_term_action="re-measure geometry immediately before acting on a mark",
        needs_human_review=True,
    )
    return ExperienceCompiler().compile_failure(
        _Event(failure_reason=failed[0].detail),
        analysis,
        recovery_trace=[{"tier": 1, "action": "re-observe and re-plan", "succeeded": traj.recovered}],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Narrated agent loop across several environments.")
    parser.add_argument(
        "--pace",
        type=float,
        default=1.8,
        help="Seconds per narration beat. Beats explaining a phase already shown are shortened.",
    )
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--scene", default="", help="Run one scene by page name, e.g. forum.html")
    parser.add_argument("--hold", type=float, default=8.0, help="Seconds to stay on the final summary.")
    parser.add_argument("--record", action="store_true", help="Record the page to a video file.")
    parser.add_argument(
        "--trace-delay",
        type=float,
        default=0.13,
        help="Seconds per executed source line while the highlight follows the code.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run the scene list N times and report campaign metrics (TSR, RTR, RSR, RTA, DA).",
    )
    args = parser.parse_args()
    make_console_safe()  # before anything prints observed page text

    from src.demos.campaign import Campaign
    from src.perception.browser_session import BrowserSession

    repo = Path(__file__).resolve().parents[1]
    out = repo / "eval_outputs" / "agent_loop" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in SCENES if not args.scene or s.page == args.scene]

    httpd, port = _start_static_server(str(repo / "env" / "mock_envs"))
    base = f"http://127.0.0.1:{port}"
    # Video capture records the page, not the desktop, so the file contains one
    # window and nothing else that happened to be open. It arrives with a later
    # branch, so ask before using it rather than assuming.
    launch_kwargs: dict[str, Any] = {"headless": args.headless}
    if args.record:
        if "record_video_dir" in inspect.signature(BrowserSession.launch).parameters:
            launch_kwargs["record_video_dir"] = str(out)
        else:
            print("  [warn] this checkout cannot record video; running without it")

    session = BrowserSession.launch(f"{base}/{scenes[0].page}", **launch_kwargs)
    session.content_html = lambda: session._page.content()  # type: ignore[attr-defined]

    print(f"\n{_LINE}\n  AGENT LOOP - observe / measure / plan / act / verify / recover\n{_LINE}")
    console = AgentConsole(session)
    results: list[tuple[Scene, Trajectory]] = []

    campaign = Campaign()
    # One ledger for the whole run. It holds the quantities every reported
    # metric is divided out of, and the panel shows it as it accumulates so no
    # figure in the final table appears without having been watched grow.
    ledger = MetricLedger()

    try:
        for repetition in range(args.repeat):
            for index, scene in enumerate(scenes):
                label = f"scene {index + 1}/{len(scenes)}"
                if args.repeat > 1:
                    label = f"rep {repetition + 1}/{args.repeat}  {label}"
                print(f"\n  --- {label}: {scene.title}")
                session.open(f"{base}/{scene.page}")
                time.sleep(0.35)  # let the page settle before observing
                if scene.page == "smart_room.html":
                    traj = run_wot_scene(
                        session,
                        console,
                        pace=args.pace,
                        trace_delay=args.trace_delay,
                        faulty=bool(scene.fault),
                        ledger=ledger,
                        familiar=index > 0,
                    )
                else:
                    traj = run_scene(
                        session,
                        console,
                        scene,
                        pace=args.pace,
                        trace_delay=args.trace_delay,
                        ledger=ledger,
                        familiar=index > 0,
                    )
                campaign.add(episode_result(scene, traj))
                if repetition == 0:
                    session.screenshot(str(out / f"scene{index + 1}_{scene.page.replace('.html', '')}.png"))
                    results.append((scene, traj))

        # Stay on a readable summary instead of closing the moment the last
        # scene ends, which gave a viewer no time to take anything in.
        solved = sum(1 for _, t in results if not t.failures() or t.recovered)
        recovered = sum(1 for _, t in results if t.recovered)
        console.open("Run complete")
        console.step(
            "DONE",
            "SUMMARY",
            f"{solved} of {len(results)} goals met, {recovered} of them only after recovering "
            f"from a failure the agent detected itself.",
            "The same loop drove every scene. Nothing about these pages was written into "
            "the agent: it looked, planned, acted, checked, and fixed itself.",
            "\n".join(
                f"{s.title}\n    goal: {s.goal_text}\n    steps: {len(t.steps)}, recovered: {t.recovered}"
                for s, t in results
            ),
        )
        console.result(
            "run", f"{solved}/{len(results)} scenes", solved == len(results), f"{solved} of {len(results)} goals met"
        )
        console.tally(ledger.counters.as_strip())
        console.banner(f"Run complete - {solved}/{len(results)} goals met, {recovered} via self-recovery", "#15803d")
        session.screenshot(str(out / "summary.png"))
        print(f"\n  holding the summary for {args.hold:.0f}s ...")
        time.sleep(args.hold)
    finally:
        console.close()
        session.close()
        httpd.shutdown()

    # The tallies first, then the metrics derived from them. In that order a
    # reader can check a figure against the counts instead of trusting it.
    print(f"\n{_LINE}\n  INTERMEDIATE QUANTITIES - what the metrics are computed from\n{_LINE}")
    print(ledger.report())
    (out / "metric_ledger.json").write_text(json.dumps(ledger.to_dict(), indent=2), encoding="utf-8")

    print(f"\n{_LINE}\n  CAMPAIGN - repeated episodes, scored against the fault injected\n{_LINE}")
    print(campaign.report())
    (out / "campaign.json").write_text(json.dumps(campaign.to_dict(), indent=2), encoding="utf-8")

    print(f"\n{_LINE}\n  SELF-EVOLUTION - what a failure taught the system\n{_LINE}")
    failing = next((t for _, t in results if t.failures()), None)
    experience = compile_experience(failing) if failing else None
    if experience is None:
        print("  No failure occurred, so there is no proposal to make.")
    else:
        print(f"  experience  : {experience.experience_id}")
        print(f"  boundary    : {experience.failure_boundary}  ({experience.failure_type})")
        print(f"  credited to : {experience.credit_assignment}")
        print(f"  immediate   : {experience.immediate_action}")
        print(f"  long term   : {experience.long_term_candidate}")
        print("\n  Compiled by src/adaptation/. The proposal stays review-gated:")
        print("  nothing is applied to the running system without a human.")
        (out / "compiled_experience.json").write_text(
            json.dumps(
                {
                    "experience_id": experience.experience_id,
                    "failure_boundary": experience.failure_boundary,
                    "failure_type": experience.failure_type,
                    "immediate_action": experience.immediate_action,
                    "long_term_candidate": experience.long_term_candidate,
                    "evidence": experience.evidence,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    (out / "trajectory.json").write_text(
        json.dumps([{"scene": s.title, **t.to_dict()} for s, t in results], indent=2), encoding="utf-8"
    )
    if args.record:
        video = to_mp4(out)
        if video:
            print(f"\n  video     : {(out / video).relative_to(repo)}")

    print(f"\n  artifacts : {out.relative_to(repo)}\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
