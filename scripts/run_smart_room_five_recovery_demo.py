#!/usr/bin/env python3
"""Run five injected recovery scenes on the live Smart Room environment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.smart_room_recovery_campaign import run_smart_room_recovery_campaign  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="artifacts/smart_room_five_recovery")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:3000")
    parser.add_argument("--directory-url", default="http://127.0.0.1:8082")
    parser.add_argument("--wot-url", default="http://127.0.0.1:8080")
    parser.add_argument("--control-url", default="http://127.0.0.1:8081")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="hide the browser; by default the five live scenes are shown for presentation",
    )
    args = parser.parse_args()

    paths = run_smart_room_recovery_campaign(
        args.output_dir,
        dashboard_url=args.dashboard_url,
        directory_url=args.directory_url,
        wot_url=args.wot_url,
        control_url=args.control_url,
        record_video=False,
        headless=args.headless,
    )
    report_path = Path(paths["report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    print("\nFive-scene live Smart Room recovery summary")
    for index, scene in enumerate(report["scenes"], start=1):
        status = "PASS" if scene["independent_oracle_verified"] else "FAIL"
        runtime = scene["runtime"]
        print(
            f"  {index}. {scene['scene']}: {status} "
            f"(state={runtime['state']}, attempts={runtime['attempts']}, "
            f"recovery={runtime['recovery_succeeded']})"
        )
    print(f"REPORT={report_path}")
    print(f"TRANSITIONS={paths['transition_ledger']}")
    print(f"FAILURES={paths['failure_ledger']}")
    print(f"PLANNER_CALLS={paths['planner_ledger']}")

    valid = (
        summary["scene_count"] == 5
        and summary["all_final_oracles_verified"]
        and summary["fault_labels_hidden_from_planner"]
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
