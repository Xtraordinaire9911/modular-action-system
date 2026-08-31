"""Single entry point for every demo in the project.

  python scripts/demo.py list          what exists, and what can run right now
  python scripts/demo.py doctor        why something cannot run, and the fix
  python scripts/demo.py run <name>    run one
  python scripts/demo.py run --all     run everything that is currently runnable

The demos themselves are unchanged and still work when invoked directly; this
only discovers them. Adding a new one means appending an entry to
``src/demos/registry.py`` -- nothing in this file needs to change.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demos.registry import (  # noqa: E402
    DEMOS,
    REPO_ROOT,
    Demo,
    DemoStatus,
    build_argv,
    capability_report,
    find,
    status_of,
)

_MARK = {
    DemoStatus.READY: "ready",
    DemoStatus.MISSING_CAPABILITY: "needs setup",
    DemoStatus.NOT_IN_CHECKOUT: "not here",
}


def cmd_list(_args: argparse.Namespace) -> int:
    # Measured, not a constant: a name longer than the column silently shifts
    # every field on its row out of alignment, and this table gets shown on a
    # projector where a misaligned row reads as a broken tool.
    width = max(len("DEMO"), *(len(d.name) for d in DEMOS))
    print(f"\n{'DEMO':<{width}} {'STATUS':<12} {'TIME':<8} TITLE")
    print("-" * 78)
    ready = 0
    for demo in DEMOS:
        status = status_of(demo)
        ready += 1 if status.ready else 0
        print(f"{demo.name:<{width}} {_MARK[status.state]:<12} {demo.duration_hint:<8} {demo.title}")
    print("-" * 78)
    print(f"{ready} of {len(DEMOS)} runnable on this machine.\n")
    print("  python scripts/demo.py run <name> --headed")
    print("  python scripts/demo.py doctor        (why something is not runnable)\n")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("\nCapabilities")
    print("-" * 78)
    for name, (ok, detail) in capability_report().items():
        print(f"  {'OK ' if ok else 'NO '} {name:<12} {detail}")

    blocked = [(d, s) for d in DEMOS if not (s := status_of(d)).ready]
    print("\nDemos that cannot run here")
    print("-" * 78)
    if not blocked:
        print("  none - everything is runnable\n")
        return 0
    for demo, status in blocked:
        print(f"  {demo.name:<18} {status.detail}")
    print()
    return 0


def _run_one(demo: Demo, *, headed: bool, extra: list[str]) -> bool:
    status = status_of(demo)
    if not status.ready:
        print(f"\n[skip] {demo.name}: {status.detail}")
        return False
    argv = build_argv(demo, headed=headed, extra=extra)
    print(f"\n{'=' * 78}\n  {demo.title}\n  {demo.summary}\n{'=' * 78}")
    print(f"$ {' '.join(argv[1:])}\n", flush=True)
    completed = subprocess.run(argv, cwd=REPO_ROOT, check=False)
    ok = completed.returncode == 0
    print(f"\n[{'ok' if ok else 'FAILED'}] {demo.name} (exit {completed.returncode})")
    return ok


def cmd_run(args: argparse.Namespace) -> int:
    if args.all:
        targets = DEMOS
    else:
        demo = find(args.name or "")
        if demo is None:
            print(f"unknown demo: {args.name!r}")
            print(f"available: {', '.join(d.name for d in DEMOS)}")
            return 2
        targets = [demo]

    results = [(d, _run_one(d, headed=args.headed, extra=args.extra)) for d in targets]
    attempted = [(d, ok) for d, ok in results if status_of(d).ready]
    if len(targets) > 1:
        print(f"\n{'=' * 78}")
        for demo, ok in attempted:
            print(f"  {'ok    ' if ok else 'FAILED'}  {demo.name}")
        print(f"{'=' * 78}")
    return 0 if all(ok for _, ok in attempted) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and run project demos.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="Show every demo and whether it can run here.").set_defaults(func=cmd_list)
    sub.add_parser("doctor", help="Explain what is missing and how to fix it.").set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="Run a demo by name.")
    run.add_argument("name", nargs="?", help="Demo name (see `list`).")
    run.add_argument("--all", action="store_true", help="Run every currently runnable demo.")
    run.add_argument("--headed", action="store_true", help="Show the browser where applicable.")
    run.add_argument("extra", nargs="*", default=[], help="Extra arguments passed through to the demo.")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    if not getattr(args, "command", None):
        return cmd_list(args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
