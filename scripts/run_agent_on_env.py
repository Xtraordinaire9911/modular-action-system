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
from src.benchmarks.web_task_planner import LLMWebTaskPlanner, RuleBasedWebTaskPlanner, subgoal_satisfied  # noqa: E402


def _parse_values(pairs: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--value expects label=text, got: {pair!r}")
        label, text = pair.split("=", 1)
        values[label.strip()] = text
    return values


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
    parser.add_argument("--goal", default="", help="Free-text goal used to pick goal-relevant buttons.")
    parser.add_argument("--value", action="append", default=[], help="Input fill as label=text (repeatable).")
    parser.add_argument(
        "--planner",
        choices=["reflex", "rule", "llm"],
        default="reflex",
        help="Action planner. 'llm' is a reserved stub and intentionally not implemented yet.",
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
    task = BenchmarkTask(args.env, args.task_id, url, args.goal, success_text=args.success_text)
    if args.planner == "llm":
        LLMWebTaskPlanner().plan(args.goal, values=values)

    print(f"launching {'headed' if args.headed else 'headless'} browser on {url}")
    session = BrowserSession.launch(url, headless=not args.headed)
    adapter = WebBenchmarkAdapter(session)
    used: list[str] = []
    history = WebPlannerHistory()
    task_plan = None
    active_subgoal = 0
    if args.planner == "rule":
        task_plan = RuleBasedWebTaskPlanner().plan(args.goal, values=values)
        print("task plan:")
        for idx, subgoal in enumerate(task_plan.subgoals, start=1):
            print(f"  {idx}. {subgoal.id}: {subgoal.description}")
    rule_planner = RuleBasedAffordancePlanner()
    try:
        for step in range(args.max_steps):
            pam = adapter.observe(task)
            session.screenshot(str(shots / f"step_{step:02d}.png"))
            print(
                f"[{step:02d}] perceived {len(pam.affordances)} affordances "
                f"(compression {pam.compression_ratio:.0%})"
            )
            if args.planner == "rule":
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
            elif args.planner == "llm":
                raise NotImplementedError(
                    "LLM planner mode is reserved; use --planner rule or --planner reflex for now"
                )
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
