"""Cross-environment fancy demo: MiniWoB++ (academic) + WebArena-style mocks (industrial sim).

Runs a curated suite spanning two environment classes in one headed browser session,
with animated periwinkle cursor trail, per-step highlighting, env badge overlay, and
a rich M1 cross-environment score table at the end.

Usage (PowerShell):
  uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3
  uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3 --skip-miniwob
  uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3 --skip-mock

Prereqs:
  uv run playwright install chromium
  # For MiniWoB++:
  git clone https://github.com/Farama-Foundation/miniwob-plusplus .external_envs/miniwob-plusplus
  uv pip install miniwob
"""

from __future__ import annotations

import argparse
import os as _os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_agent_on_env import _start_static_server  # noqa: E402
from src.benchmarks.miniwob_tasks import (  # noqa: E402
    DEMO_TASKS,
    MiniwobController,
    MockEnvController,
    run_task,
)
from src.benchmarks.mock_env_tasks import MOCK_TASKS  # noqa: E402

# ── ANSI colours (work on Win 10+ terminals) ────────────────────────────────────
_R = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_BLUE = "\033[94m"
_GREEN = "\033[92m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_YELLOW = "\033[93m"

# Enable ANSI on Windows cmd/PowerShell (no-op on Unix)
_os.system("")  # noqa: S605 — triggers ENABLE_VIRTUAL_TERMINAL_PROCESSING


def _banner() -> None:
    w = 64
    print(f"\n{_BOLD}{_BLUE}{'═'*w}{_R}")
    print(f"{_BOLD}{_BLUE}{'MODULAR ACTION SYSTEM — CROSS-ENVIRONMENT DEMO':^{w}}{_R}")
    print(f"{_BOLD}{_DIM}{'TUM Praktikum · Week 7 · Member B':^{w}}{_R}")
    print(f"{_BOLD}{_BLUE}{'═'*w}{_R}\n")


def _env_header(label: str, n_tasks: int) -> None:
    print(f"{_BOLD}{_CYAN}{'━'*4}  {label}  {'━'*(54 - len(label))}{_R}")
    print()


def _task_row(idx: int, total: int, name: str, success: bool, elapsed: float, extra: str = "") -> None:
    tick = f"{_GREEN}✓{_R}" if success else f"{_RED}✗{_R}"
    label = f"[{idx}/{total}] {name}"
    print(f"  {tick}  {label:<38} {elapsed:5.1f}s  {extra}")


def _summary_table(groups: list[dict]) -> None:
    w = 64
    print(f"\n{_BOLD}{_BLUE}{'═'*w}{_R}")
    print(f"{_BOLD}{_BLUE}{'CROSS-ENVIRONMENT M1 GENERALISATION METRIC':^{w}}{_R}")
    print(f"{_BOLD}{_BLUE}{'═'*w}{_R}")
    total_ok = total_all = 0
    for g in groups:
        ok = g["ok"]
        n = g["n"]
        pct = 100 * ok / n if n else 0
        bar = ("█" * int(pct / 5)).ljust(20)
        label = g["label"][:32]
        total_ok += ok
        total_all += n
        colour = _GREEN if ok == n else (_YELLOW if ok else _RED)
        print(f"  {label:<33} {colour}{ok}/{n}  {pct:5.1f}%  {bar}{_R}")
    print(f"  {'─'*58}")
    total_pct = 100 * total_ok / total_all if total_all else 0
    bar = ("█" * int(total_pct / 5)).ljust(20)
    colour = _GREEN if total_ok == total_all else (_YELLOW if total_ok else _RED)
    print(f"  {_BOLD}{'OVERALL (M1)':<33} {colour}{total_ok}/{total_all}  {total_pct:5.1f}%  {bar}{_R}")
    print(f"{_BOLD}{_BLUE}{'═'*w}{_R}\n")


def _run_miniwob_group(session: object, suite: list, step_delay: float, base_url: str, shots: Path) -> dict:
    """Run MiniWoB++ tasks; return group stats dict."""
    ctrl = MiniwobController(session, step_delay=step_delay, narrate=lambda m: print(f"    {_DIM}{m}{_R}"))
    outcomes: list[dict] = []
    for idx, task in enumerate(suite, 1):
        session.open(f"{base_url}/{task.name}.html")
        time.sleep(0.55)  # let page settle before clicking START gate
        ctrl.setup_badge("MiniWoB++", task.name)
        t0 = time.monotonic()
        try:
            outcome = run_task(ctrl, task)
        except Exception as exc:
            outcome = {"name": task.name, "success": False, "reward": 0.0, "utterance": "", "title": task.title}
            print(f"    {_RED}[error] {exc}{_R}")
        elapsed = time.monotonic() - t0
        _task_row(idx, len(suite), task.name, outcome["success"], elapsed, f"reward={outcome.get('reward', 0):.2f}")
        screenshot_path = shots / f"wob_{idx:02d}_{task.name}.png"
        try:
            session.screenshot(str(screenshot_path))
        except Exception:
            pass
        outcomes.append(outcome)
    ok = sum(1 for o in outcomes if o["success"])
    return {"label": "MiniWoB++ (academic benchmark)", "ok": ok, "n": len(outcomes)}


def _run_mock_group(
    session: object, tasks: list, step_delay: float, base_url: str, env_label: str, shots: Path
) -> dict:
    """Run a group of mock-env tasks sharing the same html_path; return group stats."""
    ctrl = MockEnvController(session, step_delay=step_delay, narrate=lambda m: print(f"    {_DIM}{m}{_R}"))
    outcomes: list[dict] = []
    for idx, task in enumerate(tasks, 1):
        session.open(f"{base_url}/{task.html_path}")
        time.sleep(0.45)  # let static page JS initialise
        ctrl.setup_badge(task.env_label, task.name)
        ctrl.start()  # brief render pause (no gate)
        t0 = time.monotonic()
        success = True
        try:
            task.solve(ctrl)
        except Exception as exc:
            success = False
            print(f"    {_RED}[error] {exc}{_R}")
        elapsed = time.monotonic() - t0
        # Check success via page text if a token is specified
        if success and task.success_text:
            try:
                body = session.text_content("body") or ""
                success = task.success_text.lower() in body.lower()
            except Exception:
                pass  # text_content may not be available in all contexts
        screenshot_path = shots / f"mock_{idx:02d}_{task.name}.png"
        try:
            session.screenshot(str(screenshot_path))
        except Exception:
            pass
        _task_row(idx, len(tasks), task.name, success, elapsed)
        outcomes.append({"success": success})
    ok = sum(1 for o in outcomes if o["success"])
    return {"label": env_label, "ok": ok, "n": len(outcomes)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-environment fancy demo (MiniWoB++ + WebArena-style mocks).")
    parser.add_argument("--step-delay", type=float, default=1.3, help="Seconds between visible steps.")
    parser.add_argument("--headed", dest="headed", action="store_true", default=True)
    parser.add_argument("--headless", dest="headed", action="store_false")
    parser.add_argument("--pause-between-groups", action="store_true", help="Press Enter between env groups.")
    parser.add_argument("--pause-at-end", action="store_true")
    parser.add_argument("--skip-miniwob", action="store_true", help="Skip the MiniWoB++ group.")
    parser.add_argument("--skip-mock", action="store_true", help="Skip the mock-env group.")
    parser.add_argument("--miniwob-html", default=".external_envs/miniwob-plusplus/miniwob/html")
    # Subsets of tasks (by name) for each group
    parser.add_argument("--miniwob-tasks", nargs="*", default=[], help="Task stems (default: 3-task curated subset).")
    parser.add_argument("--mock-tasks", nargs="*", default=[], help="Mock task names (default: all 6).")
    args = parser.parse_args()

    from src.perception.browser_session import BrowserSession  # lazy import (needs Playwright)

    _banner()

    shots = Path(f"eval_outputs/external_runs/fancy_demo_{datetime.now():%Y%m%d_%H%M%S}")
    shots.mkdir(parents=True, exist_ok=True)

    # ── Start static servers ─────────────────────────────────────────────────────
    mock_dir = Path(__file__).resolve().parents[1] / "env" / "mock_envs"
    mock_httpd, mock_port = _start_static_server(str(mock_dir))
    mock_base = f"http://127.0.0.1:{mock_port}"

    wob_httpd = wob_base = None
    miniwob_dir = Path(args.miniwob_html)
    if not args.skip_miniwob:
        if miniwob_dir.exists():
            wob_httpd, wob_port = _start_static_server(str(miniwob_dir))
            wob_base = f"http://127.0.0.1:{wob_port}/miniwob"
        else:
            print(f"  {_YELLOW}[warn] MiniWoB++ not found at {miniwob_dir} — skipping.{_R}")
            print(
                f"  {_DIM}Install: git clone https://github.com/Farama-Foundation/miniwob-plusplus {miniwob_dir}{_R}\n"
            )

    # ── Select task suites ───────────────────────────────────────────────────────
    # Default MiniWoB++ subset: login-user, click-dialog, click-link (varied, audience-friendly)
    default_wob = ["login-user", "click-dialog", "click-link"]
    wob_suite = [t for t in DEMO_TASKS if t.name in (args.miniwob_tasks if args.miniwob_tasks else default_wob)]
    mock_suite = [t for t in MOCK_TASKS if t.name in args.mock_tasks] if args.mock_tasks else MOCK_TASKS

    # ── Determine first URL (need one to open the browser) ───────────────────────
    first_url = (
        mock_base + "/" + mock_suite[0].html_path
        if mock_suite
        else (wob_base + "/" + wob_suite[0].name + ".html" if wob_suite else mock_base)
    )

    session = BrowserSession.launch(first_url, headless=not args.headed)
    time.sleep(0.5)

    groups: list[dict] = []
    try:
        # ── Group 1: MiniWoB++ ───────────────────────────────────────────────────
        if not args.skip_miniwob and wob_base and wob_suite:
            _env_header("MiniWoB++  —  Academic Benchmark", len(wob_suite))
            g = _run_miniwob_group(session, wob_suite, args.step_delay, wob_base, shots)
            groups.append(g)
            print()
            if args.pause_between_groups and not args.skip_mock:
                input(f"  {_DIM}Press Enter for the next environment group...{_R}\n")

        # ── Group 2–4: Mock environments (grouped by env_label) ─────────────────
        if not args.skip_mock and mock_suite:
            # Group tasks by env_label so each env gets its own header + M1 row
            env_groups: dict[str, list] = {}
            for t in mock_suite:
                env_groups.setdefault(t.env_label, []).append(t)

            env_list = list(env_groups.items())
            for gi, (label, tasks) in enumerate(env_list):
                _env_header(label, len(tasks))
                g = _run_mock_group(session, tasks, args.step_delay, mock_base, label, shots)
                groups.append(g)
                print()
                if args.pause_between_groups and gi < len(env_list) - 1:
                    input(f"  {_DIM}Press Enter for the next environment group...{_R}\n")

        # ── Summary ─────────────────────────────────────────────────────────────
        _summary_table(groups)
        print(f"  {_DIM}Screenshots: {shots}{_R}")

        if args.pause_at_end:
            input(f"\n  {_DIM}Press Enter to close the browser...{_R}")

    finally:
        session.close()
        if wob_httpd:
            wob_httpd.shutdown()
        mock_httpd.shutdown()


if __name__ == "__main__":
    main()
