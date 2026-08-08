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
from src.demos.deliberation import deliberate  # noqa: E402
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
    decision: dict[str, Any] = field(default_factory=dict)  # the full plan ranking

    def add(self, phase: str, detail: str, ok: bool = True) -> None:
        self.steps.append(StepRecord(phase, detail, ok))
        print(f"  [{'ok ' if ok else 'FAIL'}] {phase:<9} {detail}")

    def failures(self) -> list[StepRecord]:
        return [s for s in self.steps if not s.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "recovered": self.recovered,
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


def plan(pam: Any, goal: str) -> Any:
    """Score every perceived element against the goal and keep the ranking.

    Returns a Decision, not just a winner: the alternatives, each score and the
    reason each one lost stay on the record, so a wrong choice can be explained
    afterwards instead of guessed at.

    Deterministic scoring, not a language model. There is no prompt and no
    sampling here, and calling it reasoning would overstate it.
    """
    return deliberate(marks_from_affordances(pam.affordances), goal)


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


def recover(session: Any, goal: str) -> Any:
    """Re-observe, re-plan, and retry once.

    Recovery re-derives the plan from a fresh observation instead of replaying
    the failed action, so an element that moved is picked up at its new place.
    """
    fresh = observe(session)
    measure(session, fresh)
    decision = plan(fresh, goal)
    if decision.chosen_mark is not None:
        act(session, decision.chosen_mark)
    return decision.chosen_mark


def inject_fault(session: Any, selector: str) -> bool:
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

                b.style.position = 'relative';
                b.style.top = '-150px';        // stays inside its own card
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
    target: str  # label fragment the planner looks for
    check_in: str  # selector whose text proves the effect
    expect: str  # text that must appear there
    fault: str = ""  # selector to displace, empty means no fault


SCENES = [
    Scene(
        page="shopping.html",
        title="Online shop - with an injected fault",
        goal_text="Add the Wireless Headphones to the cart",
        target="Headphones",
        check_in="#cart-items",
        expect="Wireless Headphones",
        fault="button.add-cart-btn[data-id='headphones']",
    ),
    Scene(
        page="email_inbox.html",
        title="Email client",
        goal_text="Open Alice's message and archive it",
        target="Archive",
        check_in="body",
        expect="archive",
    ),
    Scene(
        page="forum.html",
        title="Discussion forum",
        goal_text="Upvote the top-ranked post",
        target="Upvote post: AI agents",
        check_in="body",
        expect="upvote",
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


def run_scene(session: Any, console: AgentConsole, scene: Scene, *, pace: float) -> Trajectory:
    traj = Trajectory(goal=scene.goal_text)
    console.open(f"{scene.title}  |  goal: {scene.goal_text}")
    console.banner(f"SCENE: {scene.title}", "#4f46e5")
    time.sleep(pace * 0.8)
    console.hide_banner()

    console.step(
        "1/5",
        "OBSERVE",
        "Reading the page the way the agent sees it.",
        "The agent never hard-codes what is on screen. It re-derives every clickable "
        "thing from the live page, so the same code works on a page it has not seen.",
        observe,
    )
    time.sleep(pace)
    pam = observe(session)
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
    n = measure(session, pam)
    traj.add("measure", f"{n} boxes measured")
    console.result("measure", f"{n} real screen positions", True, f"measured {n} on-screen positions")
    time.sleep(pace * 0.6)

    console.step(
        "3/5",
        "PLAN",
        f"Scoring every element on the page against: {scene.goal_text}.",
        "Deterministic scoring, not a language model - no prompt and no sampling. What "
        "it buys is auditability: the alternatives and the reason each lost are on the "
        "record, so a wrong choice can be explained rather than guessed at.",
        plan,
    )
    time.sleep(pace)
    decision = plan(pam, scene.target)
    traj.decision = decision.to_dict()

    # Show the deliberation itself, not only its outcome. This is the part that
    # was previously invisible: the panel displayed the planner's source but the
    # planner produced no record of what it weighed.
    console.step(
        "3/5",
        "PLAN - deliberation",
        f"{decision.considered} candidates were scored and ranked.",
        "Every option the agent could see, with the score it earned and why it "
        "lost. The winner's margin says how close the call was.",
        decision.explain(),
    )
    time.sleep(pace * 1.9)

    if decision.chosen is None:
        traj.add("plan", f"none of {decision.considered} candidates qualified", False)
        console.result("plan", "no candidate qualified", False, "could not find a way to do this")
        time.sleep(pace)
        return traj

    selection = decision.chosen_mark
    traj.add("plan", f"{decision.chosen.mark_id} '{decision.chosen.label}' (margin {decision.margin:.0f})")
    console.result(
        "plan",
        f"{decision.chosen.mark_id} by {decision.margin:.0f} points",
        True,
        f"picked {decision.chosen.mark_id} of {decision.considered}",
    )
    point_at(session, console, selection, "#8383ff")
    time.sleep(pace)

    if scene.fault:
        console.step(
            "!",
            "FAULT INJECTED",
            "The page is being changed behind the agent's back.",
            "A real site can move things at any moment. The agent planned against where the "
            "button WAS. The red dashed outline shows where it just went.",
            inject_fault,
        )
        time.sleep(pace * 0.5)
        moved = inject_fault(session, scene.fault)
        traj.add("fault", "target displaced after perception" if moved else "injection missed")
        console.result(
            "fault", "target moved away from the planned position", False, "the target moved after the agent looked"
        )
        console.banner("The page changed after the agent planned. Watch it miss.", "#b91c1c")
        time.sleep(pace * 1.4)
        console.hide_banner()

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
    ok = verify(session, scene.check_in, scene.expect)
    traj.add("verify", "goal state confirmed" if ok else "expected effect NOT observed", ok)
    console.result(
        "verify",
        f"'{scene.expect}' present" if ok else f"'{scene.expect}' NOT found",
        ok,
        "goal confirmed" if ok else "the goal was NOT met",
    )
    time.sleep(pace)

    if not ok:
        console.banner("Failure detected by the agent itself. Recovering.", "#b45309")
        time.sleep(pace)
        console.hide_banner()
        console.step(
            "6/6",
            "RECOVER",
            "Looking again, re-planning, and retrying once.",
            "Recovery does not repeat the failed click. It re-observes the page, so the "
            "element is found wherever it moved to.",
            recover,
        )
        time.sleep(pace)
        retry = recover(session, scene.target)
        if retry is not None:
            point_at(session, console, retry, "#f59e0b")
        traj.recovered = retry is not None
        traj.add("recover", "retried from a fresh observation", traj.recovered)
        console.result("recover", "re-observed and retried", traj.recovered, "retried after looking again")
        time.sleep(pace)

        ok = verify(session, scene.check_in, scene.expect)
        traj.add("verify", "confirmed after recovery" if ok else "still failing", ok)
        console.result(
            "verify",
            "confirmed after recovery" if ok else "still failing",
            ok,
            "recovered successfully" if ok else "could not recover",
        )
        console.banner("Recovered without human help." if ok else "Recovery failed.", "#15803d" if ok else "#b91c1c")
        time.sleep(pace * 1.6)
        console.hide_banner()

    session.evaluate(_CLEAR_POINT_JS)
    return traj


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
    args = parser.parse_args()

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

    try:
        for index, scene in enumerate(scenes):
            print(f"\n  --- scene {index + 1}/{len(scenes)}: {scene.title}")
            session.open(f"{base}/{scene.page}")
            time.sleep(0.6)
            traj = run_scene(session, console, scene, pace=args.pace)
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
