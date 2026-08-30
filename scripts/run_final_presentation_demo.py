#!/usr/bin/env python3
"""Run the final, chaptered presentation demo and write one evidence manifest.

Recommended live presentation (choose ``a`` at the protected booking prompt):

    .venv/bin/python scripts/run_final_presentation_demo.py \
      --profile presentation --model-mode auto --pause-between-chapters

Unattended rehearsal:

    .venv/bin/python scripts/run_final_presentation_demo.py \
      --profile presentation --model-mode recorded --headless --auto-approve --fast

Exhaustive technical rehearsal (adds the live Runtime/System-1 laboratory):

    .venv/bin/python scripts/run_final_presentation_demo.py \
      --profile complete --model-mode recorded --headless --auto-approve --fast
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.demos.final_presentation import (  # noqa: E402
    FinalDemoConfig,
    build_chapters,
    capability_snapshot,
    print_preflight,
    resolve_model_mode,
    run_final_demo,
    strict_preflight,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=("presentation", "complete"), default="presentation")
    parser.add_argument("--model-mode", choices=("auto", "live", "recorded", "skip"), default="auto")
    parser.add_argument(
        "--canonical-model",
        action="store_true",
        help="Require and use the configured text model for canonical intent plus forward/recovery planning.",
    )
    parser.add_argument("--headless", action="store_true", help="Hide browsers (rehearsal/CI only).")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve the protected canonical action without terminal input (rehearsal only).",
    )
    parser.add_argument("--pause-between-chapters", action="store_true", help="Wait for presenter handoffs.")
    parser.add_argument("--continue-on-error", action="store_true", help="Attempt later chapters after a failure.")
    parser.add_argument("--fast", action="store_true", help="Minimize narration/action delays for acceptance runs.")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated chapter IDs: canonical,runtime_lab,recovery,models,visual,adaptation.",
    )
    parser.add_argument("--check", action="store_true", help="Run strict read-only preflight and exit.")
    parser.add_argument("--plan", action="store_true", help="Print the resolved chapter commands without running them.")
    parser.add_argument("--output-dir", default="", help="Exact empty output directory (default: timestamped).")
    parser.add_argument(
        "--utterance",
        default="book room C at 15:30 and prepare it for my presentation",
    )
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:3000")
    parser.add_argument("--thing-directory-url", default="http://127.0.0.1:8082/things")
    parser.add_argument("--wot-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--control-url", default="http://127.0.0.1:8081")
    return parser


def config_from_args(args: argparse.Namespace) -> FinalDemoConfig:
    output = Path(args.output_dir).expanduser() if args.output_dir else (
        ROOT / "artifacts" / "final_presentation_demo" / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    only = tuple(item.strip() for item in args.only.split(",") if item.strip())
    return FinalDemoConfig(
        output_dir=output.resolve(),
        profile=args.profile,
        model_mode=args.model_mode,
        canonical_model=args.canonical_model,
        headless=args.headless,
        auto_approve=args.auto_approve,
        pause_between_chapters=args.pause_between_chapters,
        continue_on_error=args.continue_on_error,
        fast=args.fast,
        only=only,
        dashboard_url=args.dashboard_url,
        thing_directory_url=args.thing_directory_url,
        wot_base_url=args.wot_base_url,
        control_url=args.control_url,
        utterance=args.utterance,
    )


def main() -> int:
    args = build_parser().parse_args()
    config = config_from_args(args)
    capabilities = capability_snapshot()

    if args.check:
        checks = strict_preflight(config, capabilities)
        print_preflight(checks, capabilities)
        print(f"  effective model mode: {resolve_model_mode(config.model_mode, capabilities)}\n")
        return 0 if all(not check.required or check.ok for check in checks) else 2

    if args.plan:
        chapters = build_chapters(config, capabilities)
        print(f"\nResolved model mode: {resolve_model_mode(config.model_mode, capabilities)}")
        for index, chapter in enumerate(chapters, start=1):
            print(f"\n{index}. {chapter.chapter_id}: {chapter.title}")
            print(f"   boundary: {chapter.claim_boundary}")
            print("   command : " + (" ".join(chapter.command) if chapter.command else chapter.execution_mode))
        print()
        return 0

    try:
        code, _manifest = run_final_demo(config, capabilities=capabilities)
        return code
    except ValueError as exc:
        print(f"\nDemo configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nDemo interrupted. The latest partial manifest remains in the run directory.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
