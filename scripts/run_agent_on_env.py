"""Visibly run the action system on ANY web env that exposes a URL.

The unifying idea behind external-env support: once an environment serves a
page (a MiniWoB++ task, a self-hosted WebArena site, a live website, or our own
smart-room dashboard), the *same* perception + System-1 execution stack can
drive it. Launch headed and watch the agent perceive (DOM Transducer), choose
an affordance (reflex policy), and act (DOM executor), one step at a time.

Examples (PowerShell, one line each):
  # MiniWoB++: serve the task dir on an auto-picked free port, then drive it
  uv run python scripts/run_agent_on_env.py --serve .external_envs/miniwob-plusplus/miniwob/html --path miniwob/click-button.html --goal "click button ONE" --headed --pause-at-end
  # Any already-hosted URL (self-hosted WebArena site, live site, dashboard)
  uv run python scripts/run_agent_on_env.py --url http://localhost:7770 --goal "search" --headed

Requires Playwright Chromium once:  uv run playwright install chromium
Prefer --serve over a manual `python -m http.server`: it avoids Windows
reserved-port errors (WinError 10013) by letting the OS pick a free port.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# Make `src...` importable when run as a file (script dir, not repo root, is on
# sys.path[0]); pytest already adds the root via pythonpath, plain runs do not.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.benchmarks.reflex_policy import select_next  # noqa: E402
from src.benchmarks.rule_web_planner import RuleBasedAffordancePlanner, WebPlannerHistory  # noqa: E402
from src.benchmarks.task_spec import BenchmarkTask  # noqa: E402
from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter  # noqa: E402
from src.benchmarks.web_task_planner import RuleBasedWebTaskPlanner, subgoal_satisfied  # noqa: E402
from src.contracts.types import Affordance  # noqa: E402
from src.runtime.action_context import build_action_context  # noqa: E402
from src.runtime.affordance_controller import AffordanceController  # noqa: E402
from src.runtime.cognitive_map import CognitiveMap  # noqa: E402
from src.runtime.system2_planner import System2Planner  # noqa: E402


def _parse_values(pairs: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--value expects label=text, got: {pair!r}")
        label, text = pair.split("=", 1)
        values[label.strip()] = text
    return values


def _parse_mapping(pairs: list[str], flag: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"{flag} expects left=right, got: {pair!r}")
        left, right = pair.split("=", 1)
        mapping[left.strip()] = right.strip()
    return mapping


def _matches_affordance_target(affordance: Affordance, target: str) -> bool:
    return affordance.id == target or str(affordance.locator.get("selector", "")) == target


def _annotate_affordances(
    affordances: list[Affordance],
    *,
    bindings: dict[str, str],
    completions: set[str],
    goal_id: str,
    goal_state: str,
) -> list[Affordance]:
    annotated: list[Affordance] = []
    for affordance in affordances:
        locator = dict(affordance.locator)
        for parameter, target in bindings.items():
            if _matches_affordance_target(affordance, target):
                locator["binds_parameter"] = parameter
        if any(_matches_affordance_target(affordance, target) for target in completions):
            locator["completion_for"] = goal_id
            locator["achieves"] = goal_state
        annotated.append(
            Affordance(
                id=affordance.id,
                source=affordance.source,
                type=affordance.type,
                label=affordance.label,
                action=affordance.action,
                locator=locator,
                confidence=affordance.confidence,
                state=dict(affordance.state),
                safety_level=affordance.safety_level,
            )
        )
    return annotated


def _start_static_server(directory: str):
    """Serve ``directory`` on an OS-assigned free loopback port.

    Binding to ("127.0.0.1", 0) lets the OS pick a free port, which sidesteps
    Windows reserved-port ranges (WinNAT/Hyper-V/Docker reserve large dynamic
    ranges -> WinError 10013 on fixed ports like 8000) and "port already in use".
    Returns the running server plus its chosen port.
    """
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the action system visibly on a web environment.")
    parser.add_argument("--url", help="Full page URL to drive (omit when using --serve).")
    parser.add_argument(
        "--serve", help="Serve this local dir on an auto-picked free port (avoids reserved-port errors)."
    )
    parser.add_argument("--path", default="", help="Page path under --serve, e.g. miniwob/click-button.html.")
    parser.add_argument("--goal", default="", help="Human-readable goal label for traces.")
    parser.add_argument("--value", action="append", default=[], help="Parameter value as name=text (repeatable).")
    parser.add_argument(
        "--bind",
        action="append",
        default=[],
        help="Declare parameter binding as parameter=affordance_id_or_selector.",
    )
    parser.add_argument(
        "--complete",
        action="append",
        default=[],
        help="Declare a completion affordance id or selector; repeatable.",
    )
    parser.add_argument("--goal-state", default="", help="Structured expected effect used by the runtime planner.")
    parser.add_argument(
        "--planner",
        choices=["reflex", "rule", "runtime", "llm"],
        default="reflex",
        help=(
            "Action planner. rule uses the web demo planner; runtime uses the "
            "schema-driven environment-agnostic planner; llm is reserved."
        ),
    )
    parser.add_argument(
        "--success-text", action="append", default=[], help="Success if all fragments appear (repeatable)."
    )
    parser.add_argument("--env", default="external", help="Environment label for the trace.")
    parser.add_argument("--task-id", default="adhoc", help="Task id for the trace.")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument(
        "--step-delay", type=float, default=1.5, help="Seconds to pause between steps (for visibility)."
    )
    parser.add_argument("--headed", dest="headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument(
        "--screenshot-dir", default="", help="Where to save per-step PNGs (default eval_outputs/external_runs/<ts>)."
    )
    parser.add_argument("--pause-at-end", action="store_true", help="Keep the browser open until you press Enter.")
    args = parser.parse_args()

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright

    # Resolve the target URL, optionally spinning up our own free-port server.
    httpd = None
    if args.serve:
        httpd, port = _start_static_server(args.serve)
        url = f"http://127.0.0.1:{port}/{args.path.lstrip('/')}"
        print(f"serving {args.serve} at {url}")
    elif args.url:
        url = args.url
    else:
        raise SystemExit("provide --url, or --serve DIR [--path REL].")

    shots = Path(args.screenshot_dir or f"eval_outputs/external_runs/{datetime.now():%Y%m%d_%H%M%S}")
    shots.mkdir(parents=True, exist_ok=True)
    values = _parse_values(args.value)
    bindings = _parse_mapping(args.bind, "--bind")
    completions = {target.strip() for target in args.complete if target.strip()}
    goal_state = args.goal_state or args.goal or args.task_id
    task = BenchmarkTask(args.env, args.task_id, url, args.goal, success_text=args.success_text)
    if args.planner == "llm":
        raise NotImplementedError(
            "LLM planner mode is reserved; use --planner rule, --planner runtime, or --planner reflex"
        )

    print(f"launching {'headed' if args.headed else 'headless'} browser on {url}")
    session = BrowserSession.launch(url, headless=not args.headed)
    adapter = WebBenchmarkAdapter(session)
    used: list[str] = []
    history = WebPlannerHistory()
    task_plan = None
    active_subgoal = 0
    rule_planner = RuleBasedAffordancePlanner()
    runtime_planner = System2Planner(AffordanceController())
    runtime_used: set[tuple[str, str]] = set()
    if args.planner == "rule":
        task_plan = RuleBasedWebTaskPlanner().plan(args.goal, values=values)
        print("task plan:")
        for idx, subgoal in enumerate(task_plan.subgoals, start=1):
            print(f"  {idx}. {subgoal.id}: {subgoal.description}")
    if args.planner == "runtime":
        print("runtime planner: schema-driven, environment-agnostic")
        print(f"declared bindings: {bindings}")
        print(f"declared completions: {sorted(completions)}")
    try:
        for step in range(args.max_steps):
            pam = adapter.observe(task)
            session.screenshot(str(shots / f"step_{step:02d}.png"))
            print(
                f"[{step:02d}] perceived {len(pam.affordances)} affordances "
                f"(compression {pam.compression_ratio:.0%})"
            )
            if args.planner == "runtime":
                annotated = _annotate_affordances(
                    pam.affordances,
                    bindings=bindings,
                    completions=completions,
                    goal_id=args.task_id,
                    goal_state=goal_state,
                )
                cmap = CognitiveMap(task_id=f"{args.env}:{args.task_id}")
                cmap.update_affordances(annotated)
                context = build_action_context(cmap, request_type="goal_spec")
                visible_values = {
                    parameter: values[parameter]
                    for parameter, target in bindings.items()
                    if parameter in values and any(_matches_affordance_target(aff, target) for aff in pam.affordances)
                }
                plan = runtime_planner.plan(
                    context,
                    goal_id=args.task_id,
                    goal_state=goal_state,
                    parameters=visible_values,
                )
                pending = [
                    action
                    for action in plan.actions
                    if action.action not in {"ask_user", "done", "wait"}
                    and action.affordance_id
                    and (pam.url, action.affordance_id) not in runtime_used
                ]
                if plan.requires_escalation or not pending:
                    print(f"     no further runtime action; stopping ({plan.reason})")
                    break
                action = pending[0]
                affordance = pam.by_id(action.affordance_id)
                if affordance is None:
                    print(f"     planned affordance disappeared: {action.affordance_id}")
                    break
                value = action.value
                runtime_used.add((pam.url, action.affordance_id))
                print(f"     runtime action: {action.action} {action.affordance_id}")
            elif args.planner == "rule":
                assert task_plan is not None
                page_text = adapter.page_text()
                while active_subgoal < len(task_plan.subgoals) and subgoal_satisfied(
                    pam,
                    page_text,
                    task_plan.subgoals[active_subgoal],
                    success_text=args.success_text,
                ):
                    print(f"     subgoal done: {task_plan.subgoals[active_subgoal].id}")
                    active_subgoal += 1
                subgoal = task_plan.current(active_subgoal)
                if subgoal is None:
                    print("     all planned subgoals completed")
                    break
                print(f"     active subgoal: {subgoal.id}")
                decision = rule_planner.next_action(pam, subgoal, values=values, history=history)
                if decision.done or decision.affordance is None:
                    print(f"     no further rule action; stopping ({decision.reason})")
                    break
                affordance, value = decision.affordance, decision.value
                print(f"     rule reason: {decision.reason}")
                history = history.append(decision)
            else:
                choice = select_next(pam, args.goal, values=values, used_ids=tuple(used))
                if choice is None:
                    print("     no further affordance to act on; stopping")
                    break
                affordance, value = choice
            verb = "type" if value is not None else "click"
            print(f"     -> {verb} '{affordance.label}'  via {affordance.locator.get('selector')}")
            result = adapter.act(affordance, value=value)
            used.append(affordance.id)
            print(
                f"        {'ok' if result.success else 'FAIL'} ({result.latency_ms:.1f} ms)"
                f"{'' if result.success else ': ' + str(result.failure_reason)}"
            )
            time.sleep(max(0.0, args.step_delay))
            if adapter.is_solved(task):
                print("     success criterion met")
                break
        session.screenshot(str(shots / "final.png"))
        solved = adapter.is_solved(task)
        print(f"\nresult: {'SOLVED' if solved else 'not solved'} | steps={len(used)} | screenshots={shots}")
        if args.pause_at_end:
            input("press Enter to close the browser...")
    finally:
        session.close()
        if httpd is not None:
            httpd.shutdown()


if __name__ == "__main__":
    main()
