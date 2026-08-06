"""Visibly run + SOLVE a MiniWoB++ click-button task with the action system.

Unlike the generic runner, this uses the MiniWoB-aware adapter so it actually
clicks START, reads the instruction, clicks the correct button, and reads the
reward — across several episodes.

Example (PowerShell, one line):
  uv run python scripts/run_miniwob.py --episodes 5 --headed --pause-at-end

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
from src.benchmarks.miniwob_adapter import MiniwobAdapter  # noqa: E402
from src.benchmarks.scripted_runtime import run_scripted_task_episode  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and solve a MiniWoB++ task, visibly.")
    parser.add_argument("--miniwob-html", default=".external_envs/miniwob-plusplus/miniwob/html")
    parser.add_argument("--task", default="miniwob/click-button.html", help="Task HTML path under the html dir.")
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--headed", dest="headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument("--step-delay", type=float, default=1.0)
    parser.add_argument("--pause-at-end", action="store_true")
    args = parser.parse_args()

    from src.perception.browser_session import BrowserSession  # lazy: needs Playwright

    httpd, port = _start_static_server(args.miniwob_html)
    url = f"http://127.0.0.1:{port}/{args.task.lstrip('/')}"
    shots = Path(f"eval_outputs/external_runs/miniwob_{datetime.now():%Y%m%d_%H%M%S}")
    shots.mkdir(parents=True, exist_ok=True)
    print(f"serving {args.miniwob_html} -> {url}")

    session = BrowserSession.launch(url, headless=not args.headed)
    adapter = MiniwobAdapter(session)
    solved = 0
    try:
        for ep in range(args.episodes):
            episode = asyncio.run(
                run_scripted_task_episode(
                    task_id=f"miniwob:{args.task}:ep{ep:02d}",
                    run=adapter.run_click_button,
                    data_source="miniwob_scripted",
                )
            )
            outcome = episode.scripted_outcome
            session.screenshot(str(shots / f"ep{ep:02d}.png"))
            solved += 1 if outcome["success"] else 0
            print(
                f"[ep {ep:02d}] target={outcome['target']!r} "
                f"reward={outcome['reward']:.2f} -> {'OK' if outcome['success'] else 'miss'} "
                f"| episode={episode.result.episode_id}"
            )
            time.sleep(max(0.0, args.step_delay))
        print(f"\nsolved {solved}/{args.episodes} | screenshots={shots}")
        if args.pause_at_end:
            input("press Enter to close the browser...")
    finally:
        session.close()
        httpd.shutdown()


if __name__ == "__main__":
    main()
