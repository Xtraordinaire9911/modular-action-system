"""The command succeeded. The room did not comply.

This is the demo for the question "where is the physical part of this?", and it
needs no model, no API key, and no page interaction - which also makes it the one
demo that cannot be broken by a network problem on the day.

It shows three verification strategies reaching three different conclusions about
the same event:

* **transport** - the write returned 204, so the request was fine
* **commanded read-back** - the property reads back exactly what was asked for
* **measured read-back** - the device's own measurement never moved

Only the third one is right. The first two are what a browser agent and a
setpoint-verifying agent respectively conclude, and until recently this project
did the second one. A jammed motor reports its commanded position perfectly.

Two acts, because a divergence only means something once the audience has seen
what convergence looks like:

1. **Normal operation already takes time.** The setpoint is instant; the room
   arrives later. The projector reports power on while its lamp is still warming.
   Nothing is broken here - this is the ordinary case.
2. **Now the room stops complying.** The motor is jammed. Every status code is
   still successful and the setpoint still reads back correctly.

Honest about what it is: the servient runs at 30x real time so this fits in a
meeting, and ``GET :8081/state`` reports that itself. It models timing and
compliance. It does not model thermodynamics, sensor noise, or hardware.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demos.device_panel import DevicePanel  # noqa: E402
from src.demos.pointer_overlay import (  # noqa: E402
    AGENT_COLOR,
    FAIL_COLOR,
    OK_COLOR,
    clear_pointer,
    point_at_selector,
)

# The comparison the composite-goal runtime uses, imported rather than
# reimplemented: the predicate shown in the panel has to be the one that ran.
from src.runtime.device_goal import values_match  # noqa: E402

_LINE = "=" * 78
_RULE = "-" * 78

DEFAULT_WOT = "http://localhost:8080"
DEFAULT_CONTROL = "http://localhost:8081"
DEFAULT_DASHBOARD = "http://localhost:3000"
API_KEY = "demo"
TIMEOUT_S = 3.0


def _request(url: str, *, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    """One HTTP call, returning the status as well as the payload.

    The status is returned rather than raised on, because "the write succeeded"
    is the whole first half of this demo's point.
    """
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"X-API-Key": API_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - local demo
            raw = response.read().decode("utf-8").strip()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def read_prop(wot: str, thing: str, prop: str) -> Any:
    return _request(f"{wot}/{thing}/properties/{prop}")[1]


def write_prop(wot: str, thing: str, prop: str, value: Any) -> int:
    return _request(f"{wot}/{thing}/properties/{prop}", method="PUT", body=value)[0]


def reset_room(control: str) -> bool:
    return _request(f"{control}/reset", method="POST")[0] == 200


def inject_fault(control: str, thing: str, kind: str) -> bool:
    status, _ = _request(f"{control}/failure", method="POST", body={"thing": thing, "type": kind})
    return status in (200, 201, 204)


def physics(control: str) -> dict[str, Any]:
    """The servient's own description of how fast it is pretending to be.

    Read from the room rather than written into the narration, so the time scale
    on screen is the one actually in force.
    """
    state = _request(f"{control}/state")[1] or {}
    block = state.get("physics") if isinstance(state, dict) else None
    return block if isinstance(block, dict) else {}


def open_dashboard(url: str, *, headed: bool) -> Any:
    """Optional viewing aid; never allowed to fail the run."""
    if not headed:
        return None
    try:
        from src.perception.browser_session import BrowserSession

        session = BrowserSession.launch(url, headless=False)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        print(f"  (no browser: {type(exc).__name__}; the readings below are the result anyway)")
        return None
    time.sleep(1.8)
    return session


def _point(session: Any, region: str, label: str, colour: str) -> None:
    if session is not None:
        point_at_selector(session, region, label=label, color=colour)


def sample(
    wot: str,
    thing: str,
    commanded_prop: str,
    measured_prop: str,
    *,
    seconds: float,
    every: float = 0.35,
    on_frame: Callable[[list[tuple[float, Any, Any]]], None] | None = None,
) -> list[tuple[float, Any, Any]]:
    """Watch both properties for a while, and keep every frame.

    Both are read on the same tick. Reading them at different moments would make
    a converging device look briefly divergent, which is precisely the
    distinction this demo rests on.

    ``on_frame`` is called with the frames so far after each tick, so the panel
    fills in while the room is being watched instead of appearing complete once
    the observation is over. A table that arrives finished reads as a recording.
    """
    frames: list[tuple[float, Any, Any]] = []
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        frames.append(
            (
                round(elapsed, 2),
                read_prop(wot, thing, commanded_prop),
                read_prop(wot, thing, measured_prop),
            )
        )
        if on_frame is not None:
            on_frame(frames)
        if elapsed >= seconds:
            return frames
        time.sleep(every)


def _shown(value: Any) -> str:
    """One decimal at most, because a projector is not a debugger.

    Integrating a ramp in floating point produces readings like
    ``21.200000000000003``, which is fifteen digits of distraction in the middle
    of the one table the audience is supposed to read. The rounding is display
    only: ``sample`` keeps the raw values and the artifact records them, so
    nothing that could be checked later is lost.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:g}" if float(value).is_integer() else f"{value:.1f}"


def _print_frames(frames: list[tuple[float, Any, Any]], commanded: str, measured: str) -> None:
    print(f"    {'t':>7}  {commanded:>16}  {measured:>16}")
    for elapsed, c, m in frames:
        # Compared on the raw values, printed on the rounded ones: two readings
        # that differ in the third decimal really do differ, and a display
        # rounding must not be able to turn that into "converged".
        flag = "" if c == m else "   <- differ"
        print(f"    {elapsed:>6.2f}s  {_shown(c):>16}  {_shown(m):>16}{flag}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wot", default=DEFAULT_WOT, help="Base URL of the WoT servient.")
    parser.add_argument("--control", default=DEFAULT_CONTROL, help="Base URL of the failure control plane.")
    parser.add_argument("--dashboard", default=DEFAULT_DASHBOARD, help="Dashboard URL to open and watch.")
    parser.add_argument(
        "--headless",
        dest="headed",
        action="store_false",
        default=True,
        help="Do not open a browser; print the readings only.",
    )
    parser.add_argument("--pace", type=float, default=1.0, help="Scale every dwell (0.2 for a quick check).")
    args = parser.parse_args()

    def hold(seconds: float) -> None:
        time.sleep(seconds * args.pace)

    repo = Path(__file__).resolve().parents[1]
    out = repo / "artifacts" / "commanded_vs_measured"
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{_LINE}\n  THE COMMAND SUCCEEDED. THE ROOM DID NOT COMPLY.\n{_LINE}")

    if not reset_room(args.control):
        print(f"  the control plane at {args.control} is not answering.")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2

    block = physics(args.control)
    scale = block.get("time_scale", "?")
    print("  room reset   : yes, to its initial state")
    print(f"  time scale   : {scale}x real time, reported by the room itself at {args.control}/state")
    print("  what follows : no model, no API key, no clicking. Only reads and writes to Things.")

    session = open_dashboard(args.dashboard, headed=args.headed)
    panel = DevicePanel(session) if session is not None else None
    if panel is not None:
        panel.open()
    report: dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "time_scale": scale,
        "acts": {},
    }

    def issue(thing: str, prop: str, value: Any) -> int:
        """One write, announced on the wire before and after it is answered."""
        path = f"/{thing}/properties/{prop}"
        if panel is not None:
            panel.sent("PUT", path, value)
        code = write_prop(args.wot, thing, prop, value)
        if panel is not None:
            panel.answered(code)
        return code

    def watch(
        thing: str,
        commanded_prop: str,
        measured_prop: str,
        *,
        seconds: float,
        every: float,
    ) -> list[tuple[float, Any, Any]]:
        """Sample both properties, filling the panel table as it goes."""

        def live(frames: list[tuple[float, Any, Any]]) -> None:
            if panel is not None:
                panel.show_readings(frames, commanded=commanded_prop, measured=measured_prop)

        got = sample(args.wot, thing, commanded_prop, measured_prop, seconds=seconds, every=every, on_frame=live)
        if panel is not None:
            panel.settled(converged=values_match(got[-1][1], got[-1][2]))
        return got

    try:
        # ── Act 1 ─────────────────────────────────────────────────────────────
        print(f"\n{_RULE}\n  ACT 1 - normal operation already takes time (nothing is broken)\n{_RULE}")

        print("\n  thermostat: setpoint is instant, the room arrives later")
        if panel is not None:
            panel.begin_act(
                "ACT 1 / 3  -  NOTHING IS BROKEN HERE",
                "thermostat.targetTemperature <- 25",
                "the setpoint lands in one tick; the room needs every frame below",
            )
            # The numeric branch is the line that decides every verdict in this
            # demo, so that is where the spotlight goes. Named by content: a
            # line number would point at the wrong statement the first time
            # anyone edits the function above it.
            panel.show_source(
                values_match,
                highlight="NUMERIC_TOLERANCE",
                title="src/runtime/device_goal.py  -  the production comparator",
            )
        status = issue("thermostat", "targetTemperature", 25)
        print(f"    PUT thermostat.targetTemperature = 25   ->  HTTP {status}")
        _point(
            session,
            "[data-testid='thermostat-panel']",
            "thermostat.targetTemperature <- 25",
            AGENT_COLOR,
        )
        # Long enough for the ramp to finish. Cutting the sample off mid-climb
        # would leave Act 1 showing a divergence that never resolves, which is
        # exactly what Act 2 is supposed to be the only example of.
        thermo = watch("thermostat", "targetTemperature", "currentTemperature", seconds=5.4, every=0.6)
        _print_frames(thermo, "targetTemperature", "currentTemperature")
        arrived = values_match(thermo[-1][1], thermo[-1][2])
        if arrived:
            print("    the setpoint was reached in one tick; the measurement needed every frame above")
            print("    it did arrive - remember that, because in Act 2 it will not")
        else:
            print(f"    still climbing after {thermo[-1][0]}s: reading {_shown(thermo[-1][2])}, asked 25")
        if panel is not None:
            panel.conclude(
                "the room arrived, just later" if arrived else "still on its way",
                kind="ok" if arrived else "idle",
            )
        report["acts"]["thermostat_ramp"] = {"status": status, "frames": thermo, "converged": arrived}
        hold(1.4)

        print("\n  projector: it reports power on while the lamp is still warming")
        if panel is not None:
            panel.begin_act(
                "ACT 2 / 3  -  STILL NOTHING BROKEN",
                'projector.power <- "on"',
                "the device reports on before the lamp is lit",
            )
        status = issue("projector", "power", "on")
        print(f'    PUT projector.power = "on"              ->  HTTP {status}')
        _point(session, "[data-testid='projector-panel']", 'projector.power <- "on"', AGENT_COLOR)
        lamp = watch("projector", "power", "lamp", seconds=2.2, every=0.3)
        _print_frames(lamp, "power", "lamp")
        warming = [f for f in lamp if f[2] == "warming"]
        print(
            f"    'power' said on immediately; 'lamp' read warming for {len(warming)} of "
            f"{len(lamp)} frames before it was actually lit"
        )
        if panel is not None:
            panel.conclude(f"power said on for {len(warming)} frames before the lamp was", kind="idle")
        report["acts"]["lamp_warmup"] = {"status": status, "frames": lamp}
        hold(1.6)

        # ── Act 2 ─────────────────────────────────────────────────────────────
        print(f"\n{_RULE}\n  ACT 2 - now the room stops complying, and nothing says so\n{_RULE}")

        injected = inject_fault(args.control, "blinds", "motor_jam")
        print(f"\n  injected     : motor_jam on the blinds  ({'accepted' if injected else 'REFUSED'})")
        if panel is not None:
            panel.begin_act(
                "ACT 3 / 3  -  NOW THE ROOM STOPS COMPLYING",
                "blinds.position <- 30   (motor jammed)",
                "every status code is still successful and the setpoint still reads back",
            )
        before = read_prop(args.wot, "blinds", "measuredPosition")
        status = issue("blinds", "position", 30)
        print(f"    PUT blinds.position = 30                ->  HTTP {status}   <- a successful write")
        _point(
            session,
            "[data-testid='blinds-panel']",
            "blinds.position <- 30   measured never moves",
            FAIL_COLOR,
        )
        blinds = watch("blinds", "position", "measuredPosition", seconds=3.0, every=0.35)
        _print_frames(blinds, "position", "measuredPosition")
        commanded_now = blinds[-1][1]
        measured_now = blinds[-1][2]
        report["acts"]["motor_jam"] = {
            "status": status,
            "measured_before": before,
            "frames": blinds,
        }
        hold(1.8)

        # ── Act 3 ─────────────────────────────────────────────────────────────
        print(f"\n{_RULE}\n  ACT 3 - three ways to verify the same event, three answers\n{_RULE}")
        transport_ok = 200 <= status < 300
        # The production comparator, on the two readings, so the verdict is not a
        # second implementation that could disagree with the runtime's.
        commanded_ok = values_match(30, commanded_now)
        measured_ok = values_match(30, measured_now)
        rows = [
            ("transport (did the write succeed?)", f"HTTP {status}", transport_ok, "a browser agent stops here"),
            (
                "commanded read-back (position)",
                f"reads {commanded_now}, asked 30",
                commanded_ok,
                "a setpoint verifier stops here",
            ),
            (
                "measured read-back (measuredPosition)",
                f"reads {measured_now}, asked 30",
                measured_ok,
                "the only one that is right",
            ),
        ]
        if panel is not None:
            panel.show_verdicts(rows)
        print(f"\n  {'strategy':<40}{'evidence':<26}verdict")
        print(f"  {_RULE[:72]}")
        for name, evidence, passed, note in rows:
            print(f"  {name:<40}{evidence:<26}{'PASS' if passed else 'FAIL'}   {note}")
        report["acts"]["verdicts"] = {
            "transport_pass": transport_ok,
            "commanded_read_back_pass": commanded_ok,
            "measured_read_back_pass": measured_ok,
        }

        caught = transport_ok and commanded_ok and not measured_ok
        print()
        if caught:
            print("  Two of the three checks report success on a room that never moved.")
            print("  A purely digital surface cannot produce this shape: in a form, writing")
            print("  the field IS the effect. Here the write is accepted, the setpoint is")
            print("  correct, and the world is unchanged.")
        else:
            print("  This run did not produce the divergence it is meant to show:")
            print(f"    transport={transport_ok} commanded={commanded_ok} measured={measured_ok}")
            print("  Check that the servient exposes measuredPosition and honours motor_jam.")
        report["demonstrated_divergence"] = caught

        if panel is not None:
            panel.conclude(
                (
                    "two of three checks pass on a room that never moved"
                    if caught
                    else "this run did not produce the divergence it is meant to show"
                ),
                kind="no" if caught else "idle",
            )
        if session is not None:
            _point(
                session,
                "[data-testid='blinds-panel']",
                f"commanded {commanded_now}   measured {measured_now}   both from the room",
                FAIL_COLOR if caught else OK_COLOR,
            )
            hold(3.0)
            clear_pointer(session)
            hold(1.2)
    finally:
        if panel is not None:
            # Before the session closes, so the dashboard is left as the room's
            # own page rather than one with our furniture nailed to it.
            panel.close()
        if session is not None:
            session.close()
        # Leave nothing injected: the next thing anyone runs would inherit a
        # jammed motor and read it as a bug in their own code.
        reset_room(args.control)

    print("\n  cleanup      : room reset, fault cleared")
    print("  boundary     : this models timing and compliance. Not thermodynamics,")
    print("                 not sensor noise, not hardware. These numbers do not")
    print("                 transfer to a real building.")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"  artifact     : {(out / 'report.json').relative_to(repo)}\n{_LINE}\n")
    return 0 if report.get("demonstrated_divergence") else 1


if __name__ == "__main__":
    raise SystemExit(main())
