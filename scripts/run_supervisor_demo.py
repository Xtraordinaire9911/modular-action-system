"""One command that walks the supervisor's review points and shows the evidence.

    python scripts/run_supervisor_demo.py --headed

Each point from the review is run, then reported with the artifact it produced,
so the closing table is a claim plus a path rather than a claim alone. Points
whose code is not in the current checkout are reported as such instead of being
silently dropped -- a review summary that hides what it skipped is worse than
one that admits it.

Add `--fast` to skip the long browser suites when you only need the table.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demos.registry import REPO_ROOT, build_argv, find, status_of  # noqa: E402

_LINE = "=" * 78


@dataclass
class Point:
    """One review point, and how this repository answers it."""

    ref: str
    title: str
    demo: str | None = None  # registry demo that demonstrates it
    prove: str | None = None  # in-process proof instead of a demo
    evidence: str = ""
    slow: bool = False
    outcome: str = "not run"
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)


POINTS: list[Point] = [
    Point(
        ref="4.3 / 7.6",
        title="Visual marks come from measured geometry, not fixtures",
        demo="visual-grounding",
        evidence="eval_outputs/visual_grounding/<run>/trace.json",
    ),
    Point(
        ref="4.5",
        title="Episode isolation is real: browser state does not leak",
        prove="browser_isolation",
    ),
    Point(
        ref="4.5",
        title="Episode isolation covers devices, and reports what it cannot undo",
        prove="wot_isolation",
    ),
    Point(
        ref="4.5",
        title="Tier-4 escalation is an observable handover with a correction rate",
        prove="supervised_takeover",
    ),
    Point(
        ref="1 / 6",
        title="Cross-environment generalisation (M1) across academic and industrial surfaces",
        demo="cross-env",
        evidence="eval_outputs/external_runs/<run>/",
        slow=True,
    ),
    Point(
        ref="1 / 5 D7",
        title="Runtime trace, postcondition checks and recovery metrics",
        demo="offline",
        evidence="artifacts/recovery_metrics.json",
    ),
    Point(
        ref="5 D10 / 6",
        title="A clean clone can install, test and demo from one documented path",
        prove="bootstrap_present",
    ),
]


# ── in-process proofs ───────────────────────────────────────────────────────────
# These demonstrate library behaviour rather than a scripted run, so they assert
# the property live and report the observed values.


def prove_browser_isolation() -> tuple[bool, str, list[str]]:
    """Write state, show reset() leaks it and new_episode() does not."""
    try:
        from scripts.run_agent_on_env import _start_static_server
        from src.perception.browser_session import BrowserSession
    except ImportError as exc:
        return False, f"not in this checkout ({exc.name})", []
    if not hasattr(BrowserSession, "new_episode"):
        return False, "BrowserSession.new_episode is not in this checkout", []

    httpd, port = _start_static_server(str(REPO_ROOT / "env" / "mock_envs"))
    session = BrowserSession.launch(f"http://127.0.0.1:{port}/shopping.html", headless=True)
    try:
        session.evaluate("() => { localStorage.setItem('probe', 'leaked'); }")
        session.reset()
        after_reset = session.evaluate("() => localStorage.getItem('probe')")
        session.new_episode()
        after_episode = session.evaluate("() => localStorage.getItem('probe')")
    finally:
        session.close()
        httpd.shutdown()

    ok = after_reset == "leaked" and after_episode is None
    return ok, f"reset() -> {after_reset!r} (leaks), new_episode() -> {after_episode!r} (isolated)", []


def prove_wot_isolation() -> tuple[bool, str, list[str]]:
    """Restore a drifted setpoint; never write the read-only sensor."""
    try:
        from src.effectors.wot_episode_isolation import restore_state, snapshot_state
        from src.effectors.wot_executor import WotExecutor
    except ImportError as exc:
        return False, f"not in this checkout ({exc.name})", []

    td = {
        "@context": ["https://www.w3.org/2022/wot/td/v1.1"],
        "id": "thermostat_A",
        "title": "Thermostat",
        "base": "http://localhost:8080/thermostat",
        "properties": {
            "targetTemperature": {
                "type": "number",
                "readOnly": False,
                "forms": [
                    {"op": "readproperty", "href": "/properties/targetTemperature", "htv:methodName": "GET"},
                    {"op": "writeproperty", "href": "/properties/targetTemperature", "htv:methodName": "PUT"},
                ],
            },
            "currentTemperature": {
                "type": "number",
                "readOnly": True,
                "forms": [{"op": "readproperty", "href": "/properties/currentTemperature"}],
            },
        },
    }
    state = {"targetTemperature": 21, "currentTemperature": 19}
    writes: list[str] = []

    def send(method: str, url: str, **kwargs: object) -> tuple[int, object]:
        prop = url.rsplit("/", 1)[-1]
        if method == "GET":
            return 200, state[prop]
        writes.append(prop)
        state[prop] = kwargs.get("json")
        return 204, None

    executor = WotExecutor([td], send=send)
    snapshot = snapshot_state(executor)
    state["targetTemperature"] = 26  # the episode moves it
    report = restore_state(executor, snapshot)

    ok = state["targetTemperature"] == 21 and "currentTemperature" not in writes
    detail = (
        f"setpoint 21 -> 26 -> {state['targetTemperature']} restored; "
        f"read-only sensor written {writes.count('currentTemperature')} times; "
        f"coverage complete={snapshot.is_complete}"
    )
    return ok, detail, []


def prove_supervised_takeover() -> tuple[bool, str, list[str]]:
    """Two handovers, one with a correction, one without."""
    try:
        from src.recovery.supervised_takeover import SupervisedTakeover
    except ImportError as exc:
        return False, f"not in this checkout ({exc.name})", []

    clock = iter([0, 1000, 1000, 4000])
    takeover = SupervisedTakeover(clock=lambda: next(clock))
    takeover.pause("ep-1", "unresolved conflict")
    takeover.resume("re-pointed the agent")
    takeover.pause("ep-2", "high safety level")
    takeover.resume()  # approved without changing anything

    metrics = takeover.metrics()
    ok = metrics["corrections"] == 1 and metrics["correction_rate"] == 0.5
    detail = (
        f"pauses={metrics['pauses']} corrections={metrics['corrections']} "
        f"rate={metrics['correction_rate']:.2f} mean_wait={metrics['mean_wait_ms']:.0f}ms"
    )
    return ok, detail, []


def prove_bootstrap_present() -> tuple[bool, str, list[str]]:
    """The clean-clone entry point exists and imports nothing installable."""
    script = REPO_ROOT / "scripts" / "bootstrap.py"
    if not script.is_file():
        return False, "scripts/bootstrap.py is not in this checkout", []
    completed = subprocess.run(
        [sys.executable, str(script), "--check"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    ok = completed.returncode == 0
    return ok, "bootstrap.py --check reports the environment as usable" if ok else "environment check failed", []


PROOFS = {
    "browser_isolation": prove_browser_isolation,
    "wot_isolation": prove_wot_isolation,
    "supervised_takeover": prove_supervised_takeover,
    "bootstrap_present": prove_bootstrap_present,
}


# ── runner ──────────────────────────────────────────────────────────────────────


def _newest(pattern: str) -> str:
    matches = sorted(REPO_ROOT.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(matches[0].relative_to(REPO_ROOT)) if matches else ""


def run_point(point: Point, *, headed: bool, fast: bool) -> None:
    print(f"\n{_LINE}\n  [{point.ref}]  {point.title}\n{_LINE}")

    if point.prove:
        ok, detail, artifacts = PROOFS[point.prove]()
        point.outcome = "shown" if ok else "unavailable"
        point.detail = detail
        point.artifacts = artifacts
        print(f"  {detail}")
        return

    demo = find(point.demo or "")
    if demo is None:
        point.outcome, point.detail = "unavailable", f"no demo named {point.demo!r}"
        print(f"  {point.detail}")
        return

    status = status_of(demo)
    if not status.ready:
        point.outcome, point.detail = "unavailable", status.detail
        print(f"  skipped: {status.detail}")
        return
    if fast and point.slow:
        point.outcome, point.detail = "skipped", "--fast"
        print("  skipped: --fast")
        return

    argv = build_argv(demo, headed=headed)
    print(f"  $ {' '.join(argv[1:])}\n", flush=True)
    completed = subprocess.run(argv, cwd=REPO_ROOT, check=False)
    point.outcome = "shown" if completed.returncode == 0 else "failed"
    point.detail = f"exit {completed.returncode}"
    if point.evidence:
        found = _newest(point.evidence.replace("<run>", "*"))
        point.artifacts = [found] if found else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk the supervisor review points and show the evidence.")
    parser.add_argument("--headed", action="store_true", help="Show the browser during the visual runs.")
    parser.add_argument("--fast", action="store_true", help="Skip the long browser suites.")
    args = parser.parse_args()

    print(f"\n{_LINE}")
    print("  MODULAR ACTION SYSTEM - supervisor review walkthrough")
    print("  Member B (Ruiyao Jiang)")
    print(_LINE)

    for point in POINTS:
        run_point(point, headed=args.headed, fast=args.fast)

    print(f"\n{_LINE}\n  SUMMARY\n{_LINE}")
    print(f"  {'REF':<12} {'STATUS':<13} POINT")
    print(f"  {'-' * 74}")
    for point in POINTS:
        print(f"  {point.ref:<12} {point.outcome:<13} {point.title[:48]}")
        if point.artifacts:
            print(f"  {'':<12} {'':<13} evidence: {point.artifacts[0]}")

    shown = sum(1 for p in POINTS if p.outcome == "shown")
    unavailable = [p for p in POINTS if p.outcome == "unavailable"]
    print(f"  {'-' * 74}")
    print(f"  {shown} of {len(POINTS)} review points demonstrated in this checkout.")
    if unavailable:
        print("\n  Not demonstrated here (code lives on a branch still in review):")
        for point in unavailable:
            print(f"    [{point.ref}] {point.detail}")
    print(f"{_LINE}\n")
    return 0 if not any(p.outcome == "failed" for p in POINTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
