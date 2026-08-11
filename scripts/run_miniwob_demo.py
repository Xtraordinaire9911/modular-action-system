"""Audience-friendly MiniWoB++ demo: a curated suite of multi-step tasks.

Runs several richer tasks (type+submit, login form, password, link, ordered
clicks, dialog) back to back, with on-page highlighting, narration, and pacing
so each action is clearly visible. Prints a per-task success table at the end.

Example (PowerShell, one line):
  uv run python scripts/run_miniwob_demo.py --step-delay 1.4 --pause-between --headed

Prereqs: `uv run playwright install chromium` and the miniwob clone under
.external_envs/ (see env/RUNBOOK_external_envs.md).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.benchmarks.miniwob_tasks import DEMO_TASKS, MiniwobController, run_task  # noqa: E402
from src.benchmarks.scripted_runtime import run_scripted_task_episode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a curated MiniWoB++ demo suite, visibly.")
    parser.add_argument("--miniwob-html", default=".external_envs/miniwob-plusplus/miniwob/html")
    parser.add_argument("--tasks", nargs="*", default=[], help="Task stems to run (default: the curated suite).")
    parser.add_argument("--step-delay", type=float, default=1.4, help="Seconds between visible steps.")
    parser.add_argument("--headed", dest="headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument("--pause-between", action="store_true", help="Wait for Enter between tasks.")
    parser.add_argument("--pause-at-end", action="store_true")
    args = parser.parse_args()

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright

    suite = [t for t in DEMO_TASKS if not args.tasks or t.name in args.tasks]
    if not suite:
        raise SystemExit(f"no matching tasks; available: {', '.join(t.name for t in DEMO_TASKS)}")

    httpd, port = _start_static_server(args.miniwob_html)
    base = f"http://127.0.0.1:{port}/miniwob"
    shots = Path(f"eval_outputs/external_runs/miniwob_demo_{datetime.now():%Y%m%d_%H%M%S}")
    shots.mkdir(parents=True, exist_ok=True)
    print(f"serving {args.miniwob_html} -> {base}/<task>.html\n")

    # Launch once on the first task; reuse the isolated context for the rest.
    session = BrowserSession.launch(f"{base}/{suite[0].name}.html", headless=not args.headed)
    controller = MiniwobController(session, step_delay=args.step_delay)
    outcomes: list[dict] = []
    try:
        for index, task in enumerate(suite, 1):
            print(f"== Task {index}/{len(suite)}: {task.title} ({task.name}) ==")
            session.open(f"{base}/{task.name}.html")
            time.sleep(0.6)  # let the page settle before clicking START
            episode = asyncio.run(
                run_scripted_task_episode(
                    task_id=f"miniwob:{task.name}",
                    run=lambda task=task: run_task(controller, task),
                    data_source="miniwob_scripted",
                )
            )
            outcome = episode.scripted_outcome
            print(f"   instruction: {outcome['utterance']}")
            print(
                f"   reward={outcome['reward']:.2f} -> {'SOLVED' if outcome['success'] else 'missed'} "
                f"| episode={episode.result.episode_id}\n"
            )
            session.screenshot(str(shots / f"{index:02d}_{task.name}.png"))
            outcomes.append(outcome)
            if args.pause_between and index < len(suite):
                input("press Enter for the next task...")

        solved = sum(1 for o in outcomes if o["success"])
        print("================ demo summary ================")
        for o in outcomes:
            print(f"  [{'OK' if o['success'] else '  '}] {o['name']:24} reward={o['reward']:.2f}")
        print(f"solved {solved}/{len(outcomes)} | screenshots={shots}")
        if args.pause_at_end:
            input("press Enter to close the browser...")
    finally:
        session.close()
        httpd.shutdown()


if __name__ == "__main__":
    main()
