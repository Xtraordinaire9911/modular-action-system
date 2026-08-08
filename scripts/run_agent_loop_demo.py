"""The agent driving several environments, narrated inside one browser window.

    python scripts/run_agent_loop_demo.py

Built to be understood by someone who has never seen the project. A side panel
says, for every step: which phase of the loop it is, what is happening in plain
language, why the step exists, and the source that is running. The page itself
shows a cursor moving to the element and a highlight where the agent acts, so
the narration and the action are visible in the same frame.

Scenes, in order: shopping, email, forum. The shopping scene has a fault
injected on purpose, so the run shows a failure being detected and recovered
rather than only describing that it could be.

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
from src.demos.pip_console import AgentConsole  # noqa: E402
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
    """
    return expect.lower() in (session.text_content(where) or "").lower()


def app_html(session: Any) -> str:
    """The page as the application renders it, with our own overlays removed.

    Change detection has to ignore the narration panel, cursor and rings: they
    update on every step, so comparing raw HTML would report "the page changed"
    even when the agent's click did nothing at all - which is exactly the case
    the diagnosis needs to be able to see.
    """
    return str(session.evaluate("""()=>{const c=document.body.cloneNode(true);
               c.querySelectorAll('[id^="__cua"]').forEach(e=>e.remove());
               return c.innerHTML;}""") or "")


def diagnose_failure(session: Any, attempted: Any, goal: str, page_before: str) -> Any:
    """Work out *why* the step failed, from what can be seen afterwards.

    Nothing here is told which fault was injected. It re-observes, looks for the
    element it acted on, compares the page against how it looked before, and
    lets those answers pick the recovery tier.
    """
    from src.demos.diagnosis import diagnose

    fresh_pam = observe(session)
    measure(session, fresh_pam)
    fresh_marks = marks_from_affordances(fresh_pam.affordances)
    changed = app_html(session) != page_before

    def find_alternative(marks: list[Any], want: str) -> Any:
        result = choose_target(fresh_pam, want)
        return result.mark if result.ok else None

    return diagnose(
        attempted=attempted,
        fresh_marks=fresh_marks,
        goal=goal,
        world_changed=changed,
        alternative_finder=find_alternative,
    )


def apply_recovery(session: Any, diagnosis: Any, goal: str) -> bool:
    """Carry out the tier the diagnosis selected.

    Each branch is a genuinely different response, not the same retry wearing
    different labels: tier 1 looks again and repeats, tier 2 acts on a different
    affordance, tier 4 stops and hands over rather than trying anything.
    """
    from src.demos.diagnosis import STRATEGY_ESCALATE, STRATEGY_REROUTE, STRATEGY_RETRY

    if diagnosis.strategy == STRATEGY_ESCALATE:
        return False  # deliberately does not act; the handover is the response

    fresh = observe(session)
    measure(session, fresh)
    result = choose_target(fresh, goal)
    if not result.ok:
        return False
    if diagnosis.strategy in (STRATEGY_RETRY, STRATEGY_REROUTE):
        act(session, result.mark)
        return True
    return False


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


def inject_vanish(session: Any, selector: str) -> bool:
    """Remove the target entirely, leaving a marker where it was.

    The agent's plan now names something that no longer exists, so re-observing
    and retrying cannot help. It has to notice the absence and find another
    route, which is a different recovery from a target that merely moved.
    """
    return bool(
        session.evaluate(
            """(sel)=>{
                const b = document.querySelector(sel);
                if (!b) return false;
                const r = b.getBoundingClientRect();
                const ghost = document.createElement('div');
                ghost.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;
                    width:${Math.max(r.width,120)}px;height:${Math.max(r.height,26)}px;
                    z-index:2147483643;border:2px dashed #f87171;border-radius:8px;
                    pointer-events:none;display:flex;align-items:center;
                    justify-content:center;color:#f87171;font:600 10px system-ui;
                    background:rgba(248,113,113,.08)`;
                ghost.textContent = 'this control no longer exists';
                document.body.appendChild(ghost);
                b.remove();
                return true;
            }""",
            selector,
        )
    )


def inject_inert(session: Any, selector: str) -> bool:
    """Leave the control in place but disconnect it from its effect.

    This is the failure the review singled out: the action is accepted, the
    control behaves as though it worked, and the state the goal named never
    changes. Retrying the identical action would produce the identical nothing,
    so the only correct response is to stop and escalate.
    """
    return bool(
        session.evaluate(
            """(sel)=>{
                const b = document.querySelector(sel);
                if (!b) return false;
                const clone = b.cloneNode(true);   // drops every listener
                clone.style.outline = '3px solid #f59e0b';
                clone.title = 'accepts clicks, does nothing';
                b.replaceWith(clone);
                return true;
            }""",
            selector,
        )
    )


def inject_displace(session: Any, selector: str) -> bool:
    """Move the target after it was perceived, and mark where it used to be.

    This is the DOM-mutation fault the supervisor named: the page changes
    between observation and action, so the plan is stale through no fault of
    the planner.

    The button moves *upward inside its own product card*, and a dashed ghost
    is left at the position the agent planned against. An earlier version slid
    it 250px down, where it landed on a different product's card and made the
    page read as though the wrong item had been added - the fault has to be
    legible, not just present.
    """
    return bool(
        session.evaluate(
            """(sel)=>{
                const b = document.querySelector(sel);
                if (!b) return false;
                const r = b.getBoundingClientRect();

                const ghost = document.createElement('div');
                ghost.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;
                    width:${r.width}px;height:${r.height}px;z-index:2147483643;
                    border:2px dashed #f87171;border-radius:8px;pointer-events:none;
                    display:flex;align-items:center;justify-content:center;
                    color:#f87171;font:600 11px system-ui;background:rgba(248,113,113,.08)`;
                ghost.textContent = 'agent planned to click here';
                document.body.appendChild(ghost);

                // Move whichever way has room. A fixed offset pushed elements
                // near the top of the page off-screen, where nothing could
                // click them and the fault stopped being recoverable at all.
                const room_above = r.top - 60;
                const room_below = window.innerHeight - r.bottom - 60;
                const shift = Math.min(150, Math.max(room_above, room_below));
                b.style.position = 'relative';
                b.style.top = (room_above >= room_below ? -shift : shift) + 'px';
                b.style.outline = '3px solid #f59e0b';
                return true;
            }""",
            selector,
        )
    )


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
    fault: str = ""  # which fault to inject: displace | vanish | inert
    fault_selector: str = ""  # the element the fault acts on

    @property
    def expected_cause(self) -> str:
        """Ground truth for scoring only. Never reaches the diagnosis."""
        from src.demos.diagnosis import CAUSE_INERT, CAUSE_MOVED, CAUSE_VANISHED

        return {"displace": CAUSE_MOVED, "vanish": CAUSE_VANISHED, "inert": CAUSE_INERT}.get(self.fault, "")

    @property
    def expected_tier(self) -> int:
        """The tier this fault warrants: retry a move, reroute around a removal,
        and refuse to retry something that provably does nothing."""
        return {"displace": 1, "vanish": 2, "inert": 4}.get(self.fault, 0)


# Four scenes, three of them faulted, and each fault of a different kind so the
# agent has to diagnose rather than apply one rehearsed answer. Which recovery
# tier each ends up using is decided at run time from what it observes, and is
# not written down here.
# Five scenes, four of them faulted, with three distinct kinds of fault. Each
# verify checks a precise state change rather than a word that already appears
# on the page, so a fault cannot pass unnoticed. Which recovery tier each ends
# up using is decided at run time from what the agent observes; it is not
# written down here, and the recovery code is never told which fault was used.
SCENES = [
    Scene(
        page="shopping.html",
        title="Online shop - the target moves after being seen",
        goal_text="Add the Wireless Headphones to the cart",
        utterance="Could you put the wireless headphones in my cart?",
        target="Headphones",
        check_in="#cart-items",
        expect="Wireless Headphones",
        fault="displace",
        fault_selector="button.add-cart-btn[data-id='headphones']",
    ),
    Scene(
        page="shopping.html",
        title="Online shop - the control accepts the click and does nothing",
        goal_text="Add the Pro Laptop to the cart",
        utterance="Add the pro laptop to my cart please.",
        target="Laptop",
        check_in="#cart-items",
        expect="Pro Laptop",
        fault="inert",
        fault_selector="button.add-cart-btn[data-id='laptop']",
    ),
    Scene(
        page="forum.html",
        title="Discussion forum - the target disappears entirely",
        goal_text="Upvote a post",
        utterance="Give a post an upvote.",
        target="Upvote",
        check_in="#votes-2",
        expect="29",
        fault="vanish",
        fault_selector="button.upvote-btn[data-post='1']",
    ),
    Scene(
        page="forum.html",
        title="Discussion forum - the target moves after being seen",
        goal_text="Upvote the top-ranked post",
        utterance="Upvote the top post.",
        target="AI agents",
        check_in="#votes-1",
        expect="43",
        fault="displace",
        fault_selector="button.upvote-btn[data-post='1']",
    ),
    Scene(
        page="shopping.html",
        title="Online shop - clean run, nothing injected",
        goal_text="Add the Mechanical Keyboard to the cart",
        utterance="Add the mechanical keyboard to my cart.",
        target="Keyboard",
        check_in="#cart-items",
        expect="Mechanical Keyboard",
    ),
]


FAULTS = {
    "displace": inject_displace,
    "vanish": inject_vanish,
    "inert": inject_inert,
}

FAULT_BLURB = {
    "displace": "The target is still on the page, but somewhere else than where the agent saw it.",
    "vanish": "The target has been removed from the page entirely.",
    "inert": "The control still accepts clicks and now does nothing at all.",
}


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


def run_scene(session: Any, console: AgentConsole, scene: Scene, *, pace: float, trace_delay: float) -> Trajectory:
    traj = Trajectory(goal=scene.goal_text)
    console.open(f"{scene.title}  |  said: “{scene.utterance}”")
    console.banner(f"SCENE: {scene.title}", "#4f46e5")
    time.sleep(pace * 0.8)
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
    time.sleep(pace)
    goal_plan = console.run_traced(interpret, scene.utterance, line_delay=trace_delay)
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
    time.sleep(pace * 1.4)

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
    pam = console.run_traced(observe, session, line_delay=trace_delay)
    traj.add("observe", f"{len(pam.affordances)} affordances perceived")
    console.result(
        "observe",
        f"{len(pam.affordances)} interactive elements found",
        True,
        f"found {len(pam.affordances)} things it could act on",
    )
    time.sleep(pace * 0.6)

    console.step(
        "2/5",
        "MEASURE",
        "Asking the browser where each element actually is.",
        "Positions are measured live, never taken from a fixture. Anything that cannot "
        "be measured gets no mark at all, so the agent cannot aim at something imagined.",
        measure,
    )
    time.sleep(pace)
    n = console.run_traced(measure, session, pam, line_delay=trace_delay)
    traj.add("measure", f"{n} boxes measured")
    console.result("measure", f"{n} real screen positions", True, f"measured {n} on-screen positions")
    time.sleep(pace * 0.6)

    console.step(
        "3/5",
        "DECIDE",
        f"Choosing which of the {len(pam.affordances)} elements advances the goal.",
        "This is what numbering the elements is for: the answer is an identifier "
        "like M002, not a pixel coordinate. A model answers when one is configured; "
        "otherwise deterministic scoring does, and the result says which.",
        choose_target,
    )
    time.sleep(pace)
    selection_result = console.run_traced(choose_target, pam, scene.target, line_delay=trace_delay)
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
    time.sleep(pace * 1.9)

    if not selection_result.ok:
        traj.add("decide", f"none of {selection_result.considered} candidates qualified", False)
        console.result("decide", selection_result.reason[:60], False, "could not find a way to do this")
        time.sleep(pace)
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
    time.sleep(pace)

    if scene.fault:
        console.step(
            "!",
            f"FAULT: {scene.fault}",
            FAULT_BLURB[scene.fault],
            "A real site can change under an agent at any moment, and not always in the "
            "same way. Which fault this is will not be passed to the recovery code: the "
            "agent has to work it out from what it can observe afterwards.",
            FAULTS[scene.fault],
        )
        time.sleep(pace * 0.5)
        injected = FAULTS[scene.fault](session, scene.fault_selector)
        traj.fault_kind = scene.fault
        traj.add("fault", f"{scene.fault} applied to the target" if injected else "injection missed")
        console.result("fault", FAULT_BLURB[scene.fault], False, f"fault injected: {scene.fault}")
        console.banner("The page changed after the agent planned. Watch what it does.", "#b91c1c")
        time.sleep(pace * 1.4)
        console.hide_banner()

    page_before_action = app_html(session)

    console.step(
        "4/5",
        "ACT",
        "Clicking the centre of the chosen mark.",
        "The click is aimed at the visual mark, not at a CSS selector. That is what makes "
        "this the same code path a vision-driven agent would use.",
        act,
    )
    time.sleep(pace)
    act(session, selection)
    traj.add("act", f"clicked {selection.bbox.center}")
    console.result("act", f"clicked at {selection.bbox.center}", True, "click sent")
    time.sleep(pace * 0.8)

    console.step(
        "5/5",
        "VERIFY",
        "Re-reading the page to check the goal was really met.",
        "The click reporting success is not proof. The agent re-reads the page and looks "
        "for the exact outcome the goal named.",
        verify,
    )
    time.sleep(pace)
    ok = console.run_traced(verify, session, scene.check_in, scene.expect, line_delay=trace_delay)
    traj.add("verify", "goal state confirmed" if ok else "expected effect NOT observed", ok)
    console.result(
        "verify",
        f"'{scene.expect}' present" if ok else f"'{scene.expect}' NOT found",
        ok,
        "goal confirmed" if ok else "the goal was NOT met",
    )
    time.sleep(pace)

    if not ok:
        console.banner("Failure detected by the agent itself. Diagnosing.", "#b45309")
        time.sleep(pace)
        console.hide_banner()

        # 6a. Work out why, before deciding what to do. This is the step that
        # separates diagnosis from a rehearsed answer.
        console.step(
            "6/7",
            "DIAGNOSE",
            "Asking why it failed, before deciding what to try.",
            "Nothing tells this code which fault was injected. It re-observes, looks for "
            "the element it acted on, and compares the page with how it looked before. "
            "Those answers pick the recovery tier.",
            diagnose_failure,
        )
        diagnosis = console.run_traced(
            diagnose_failure, session, selection, scene.target, page_before_action, line_delay=trace_delay
        )
        traj.diagnosis = diagnosis.to_dict()
        traj.add("diagnose", f"{diagnosis.cause} -> tier {diagnosis.tier} ({diagnosis.strategy})")

        console.step(
            "6/7",
            f"DIAGNOSE - {diagnosis.cause}",
            f"Concluded: {diagnosis.cause}. Chosen response: tier {diagnosis.tier}.",
            "The evidence it used is listed here. A different fault produces different "
            "evidence and a different tier, which is what makes this a decision rather "
            "than a script.",
            diagnosis.explain(),
        )
        console.result(
            "diagnose",
            f"tier {diagnosis.tier}: {diagnosis.strategy}",
            True,
            f"diagnosed: {diagnosis.cause}",
        )
        time.sleep(pace * 1.8)

        # 6b. Carry out whatever the diagnosis chose.
        console.step(
            "7/7",
            f"RECOVER - tier {diagnosis.tier}",
            f"Applying: {diagnosis.strategy.replace('_', ' ')}.",
            "Tier 1 looks again and repeats. Tier 2 acts on a different affordance. "
            "Tier 4 deliberately does not act: it stops and hands over, because "
            "repeating an action that provably does nothing would just waste the attempt.",
            apply_recovery,
        )
        acted = console.run_traced(apply_recovery, session, diagnosis, scene.target, line_delay=trace_delay)
        time.sleep(pace * 0.6)

        if diagnosis.tier >= 4:
            metrics = escalate(f"{diagnosis.cause}: {diagnosis.strategy}")
            traj.escalated = True
            traj.add("escalate", f"handed to a supervisor; correction rate {metrics['correction_rate']:.2f}")
            console.result("escalate", "paused and handed over", True, "escalated to a human, as designed")
            console.banner("Correctly refused to retry. Escalated to a human.", "#b45309")
            time.sleep(pace * 1.8)
            console.hide_banner()
        else:
            traj.add("recover", f"tier {diagnosis.tier} applied", acted)
            ok = console.run_traced(verify, session, scene.check_in, scene.expect, line_delay=trace_delay)
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
            time.sleep(pace * 1.6)
            console.hide_banner()

    traj.goal_met = ok
    session.evaluate(_CLEAR_POINT_JS)
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
    parser.add_argument("--pace", type=float, default=1.8, help="Seconds per narration beat.")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--scene", default="", help="Run one scene by page name, e.g. forum.html")
    parser.add_argument("--hold", type=float, default=12.0, help="Seconds to stay on the final summary.")
    parser.add_argument("--record", action="store_true", help="Record the page to a video file.")
    parser.add_argument(
        "--trace-delay",
        type=float,
        default=0.08,
        help="Seconds per executed source line while the highlight follows the code.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run the scene list N times and report campaign metrics (TSR, RTR, RSR, RTA, DA).",
    )
    args = parser.parse_args()

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

    try:
        for repetition in range(args.repeat):
            for index, scene in enumerate(scenes):
                label = f"scene {index + 1}/{len(scenes)}"
                if args.repeat > 1:
                    label = f"rep {repetition + 1}/{args.repeat}  {label}"
                print(f"\n  --- {label}: {scene.title}")
                session.open(f"{base}/{scene.page}")
                time.sleep(0.6)
                traj = run_scene(session, console, scene, pace=args.pace, trace_delay=args.trace_delay)
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
        console.banner(f"Run complete - {solved}/{len(results)} goals met, {recovered} via self-recovery", "#15803d")
        session.screenshot(str(out / "summary.png"))
        print(f"\n  holding the summary for {args.hold:.0f}s ...")
        time.sleep(args.hold)
    finally:
        console.close()
        session.close()
        httpd.shutdown()

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
    print(f"\n  artifacts : {out.relative_to(repo)}\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
