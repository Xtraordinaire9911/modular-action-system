"""Can the model read the room's panels, or only the number the room was told?

    python scripts/eval_room_vision.py             # three repetitions per condition
    python scripts/eval_room_vision.py --reps 5    # more repetitions for variance

scripts/eval_model_value.py already asks whether a vision model catches a page
that is right in the DOM and wrong on screen. It answers that on a shopping page
where the fault has to be manufactured: text painted over, moved offscreen, made
transparent. Those are tricks, and a reviewer is entitled to ask whether the
capability survives outside a rigged page.

The smart-room dashboard removes the need to rig anything. Each device panel now
prints what the device was *told* above what it *measures*, so a room that did
not comply produces the shape by itself: Position 30 %, Measured 100 %. No CSS
trick, no hidden element, and the requested number really is in the document,
which is what makes it the sharpest version of this project's central claim. A
DOM oracle reading ``blinds-position`` finds 30 and reports success. The screen
says the blind never moved.

Four conditions, each repeated, each a different relation between the two
readings:

  converged      the device arrived, so the readings agree           -> yes
  diverging      the device is mid travel, so they differ            -> no
  non compliant  motor_jam: commanded correct, measurement frozen    -> no
  lamp warming   power reads on while the lamp still reads warming   -> no

Only the first should be answered yes. The three answered no are not
interchangeable: diverging and lamp warming are ordinary operation, and a model
that reported those as non-compliance would have the room escalating every time
it was asked to do anything. What separates all three from converged is the same
thing, and it is the whole question: the lower reading, not the upper one.

A rate here is only meaningful with its denominator, so three things are counted
out rather than folded in. A trial counts only once the panel was *seen* holding
its shape at the moment of capture. An answer the model declined is neither a
detection nor a false alarm, because it is neither. And a call that came back
with nothing usable, whether it failed in transport or returned JSON that will
not parse, is a reliability cost rather than an opinion.

Needs the room running (docker: dashboard :3000, Things :8080, control :8081) and
a vision key in .env.local. With no model configured this reports "not measured"
and keeps whatever the last real run measured.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The room's own HTTP verbs, imported rather than reimplemented: a second copy of
# "how to write a property" is a second thing that can disagree with the demo
# about what the API key or the timeout is.
from scripts.run_commanded_vs_measured import (  # noqa: E402
    inject_fault,
    read_prop,
    reset_room,
    write_prop,
)
from src.perception.vlm_observer import VlmObserver, available_vision_client  # noqa: E402
from src.planner.environment_binding import DEVICE_VIEWS, DeviceView  # noqa: E402

_LINE = "=" * 78

DEFAULT_WOT = "http://localhost:8080"
DEFAULT_CONTROL = "http://localhost:8081"
DEFAULT_DASHBOARD = "http://localhost:3000"

# The dashboard re-reads the Things on its own fixed interval, so the room can
# pass through a state the page never displays. Everything below therefore polls
# the *page*, not the room, and re-creates a short-lived shape when a poll did
# not land inside it. The lamp is the case that forces this: it warms for about
# a second, which is shorter than one of those intervals, so an attempt has to
# start the warming at a different moment each time (PHASE_STEP_S) until one of
# them straddles a poll. Ten steps of a quarter second sweep well past any
# plausible interval without this script having to know what it is.
PANEL_POLL_S = 0.12
PANEL_WINDOW_S = 9.0
PHASE_STEP_S = 0.25
SETUP_ATTEMPTS = 10

NON_COMPLIANT = "non_compliant"


@dataclass(frozen=True)
class Room:
    """Where the three halves of the running room answer."""

    wot: str = DEFAULT_WOT
    control: str = DEFAULT_CONTROL
    dashboard: str = DEFAULT_DASHBOARD


@dataclass(frozen=True)
class PanelReading:
    """The two lines of one device panel, as the page rendered them.

    Held as the strings the DOM contained rather than the numbers the room
    contained. The text is what was in the image; a trial graded against the
    room's value would be grading a picture nobody took.
    """

    commanded: str
    measured: str
    baseline_measured: str  # the lower line before the write, so travel is detectable


def arrived(reading: PanelReading) -> bool:
    """The two readings agree, so the device is where it was told to be."""
    return bool(reading.commanded) and reading.commanded == reading.measured


def still_travelling(reading: PanelReading) -> bool:
    """They differ, and the lower reading has moved off where it started."""
    return (
        bool(reading.measured)
        and reading.commanded != reading.measured
        and reading.measured != reading.baseline_measured
    )


def never_moved(reading: PanelReading) -> bool:
    """They differ, and the lower reading is exactly where it began.

    Split from :func:`still_travelling` deliberately. Both produce the same
    picture, and from the image alone they cannot be told apart, which is a fact
    about the dashboard rather than a defect in it. The difference is in how the
    panel got there, and only the setup knows that. One shared "they differ"
    predicate would have let a jam that quietly healed be recorded as a
    non-compliance trial and inflate the number this whole script exists to
    report.
    """
    return (
        bool(reading.measured)
        and reading.commanded != reading.measured
        and reading.measured == reading.baseline_measured
    )


def lamp_not_lit(reading: PanelReading) -> bool:
    """Power reads on and the lamp is still warming: told yes, not yet yes."""
    return reading.commanded == "on" and reading.measured == "warming"


# DEVICE_VIEWS' projector entry asks whether Power reads on, and Power is the
# line the write sets: a projector told "on" reads on whatever its lamp is doing,
# so that question is answered yes by a panel showing exactly the failure this
# trial is about. Region and dataclass are taken from it unchanged; only the
# claim moves down to the reading that is allowed to disagree with the command.
PROJECTOR_LAMP_VIEW = DeviceView(
    goal_state="projector_on",
    region=DEVICE_VIEWS["projector_on"].region,
    value_selector="[data-testid='projector-lamp']",
    visual_claim="a projector panel whose Lamp reads {value}",
)


@dataclass(frozen=True)
class RoomCondition:
    """One panel state, how to produce it, and what a correct reading of it is."""

    name: str
    view: DeviceView  # names the panel to crop and the claim to ask about it
    commanded_selector: str  # the upper line; a DeviceView names only the lower one
    thing: str
    commanded_property: str
    measured_property: str
    value: Any
    expected_answer: bool
    panel_shows: Callable[[PanelReading], bool]
    fault: str = ""  # injected on the same Thing before the write

    def question(self) -> str:
        return self.view.question_for(self.value)


CONDITIONS: tuple[RoomCondition, ...] = (
    RoomCondition(
        name="converged",
        view=DEVICE_VIEWS["temperature_set"],
        commanded_selector="[data-testid='target-temp']",
        thing="thermostat",
        commanded_property="targetTemperature",
        measured_property="currentTemperature",
        value=22,
        expected_answer=True,
        panel_shows=arrived,
    ),
    RoomCondition(
        name="diverging",
        view=DEVICE_VIEWS["temperature_set"],
        commanded_selector="[data-testid='target-temp']",
        thing="thermostat",
        commanded_property="targetTemperature",
        measured_property="currentTemperature",
        # Far enough that the ramp outlasts several dashboard polls. A two degree
        # request converges inside a single poll interval, so the panel would be
        # photographed after it had already arrived and this would quietly become
        # a second `converged` trial reported under the wrong name.
        value=30,
        expected_answer=False,
        panel_shows=still_travelling,
    ),
    RoomCondition(
        name=NON_COMPLIANT,
        view=DEVICE_VIEWS["blinds_set"],
        commanded_selector="[data-testid='blinds-position']",
        thing="blinds",
        commanded_property="position",
        measured_property="measuredPosition",
        value=30,
        expected_answer=False,
        panel_shows=never_moved,
        fault="motor_jam",
    ),
    RoomCondition(
        name="lamp_warming",
        view=PROJECTOR_LAMP_VIEW,
        commanded_selector="[data-testid='projector-power']",
        thing="projector",
        commanded_property="power",
        measured_property="lamp",
        value="on",
        expected_answer=False,
        panel_shows=lamp_not_lit,
    ),
)


@dataclass
class RoomTrial:
    """One attempt at one condition, and everything needed to audit the grade."""

    condition: str
    expected_answer: bool
    question: str = ""
    established: bool = False
    panel_commanded: str = ""
    panel_measured: str = ""
    # What the Things themselves reported at the moment of capture. Recorded so
    # the artifact can be read without the room still running, and so the claim
    # "the requested number is in the document" is checkable rather than asserted.
    room_commanded: Any = None
    room_measured: Any = None
    model_says_met: bool | None = None  # None when the model gave no usable answer
    confidence: float = 0.0
    evidence: str = ""
    source: str = ""
    note: str = ""

    @property
    def model_correct(self) -> bool | None:
        return None if self.model_says_met is None else self.model_says_met == self.expected_answer

    @property
    def graded(self) -> bool:
        """Countable towards a rate: the panel held its shape and the model answered."""
        return self.established and self.model_says_met is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "expected_answer": self.expected_answer,
            "question": self.question,
            "established": self.established,
            "panel_commanded": self.panel_commanded,
            "panel_measured": self.panel_measured,
            "room_commanded": self.room_commanded,
            "room_measured": self.room_measured,
            "model_says_met": self.model_says_met,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "source": self.source,
            "model_correct": self.model_correct,
            "graded": self.graded,
            "note": self.note,
        }


@dataclass
class Report:
    trials: list[RoomTrial] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return summarise(self.trials)


def _rate(hits: int, total: int) -> float | None:
    """None, not 0.0, when nothing was measured.

    scripts/eval_model_value.py returns 0.0 here and prints the denominator
    beside it, which is safe as long as the reader looks at both. This file is
    cited for a detection rate, and a 0% meaning "never tested" sitting in the
    same field as a 0% meaning "never caught" is the exact confusion the
    carry-forward rule elsewhere in that script exists to prevent.
    """
    return round(hits / total, 4) if total else None


def summarise(trials: list[RoomTrial]) -> dict[str, Any]:
    """The rates this run actually measured, each beside the denominator it got."""
    established = [t for t in trials if t.established]
    graded = [t for t in established if t.graded]
    # "The panel does not show arrival" is a property of what was on screen, not
    # of whether a fault was injected: the thermostat mid ramp is faultless and
    # its panel still shows a device that has not got there. Defining detection
    # off the fault label would count every diverging trial as a miss.
    non_arrival = [t for t in graded if not t.expected_answer]
    arrival = [t for t in graded if t.expected_answer]
    jammed = [t for t in non_arrival if t.condition == NON_COMPLIANT]
    return {
        "trials_attempted": len(trials),
        # The denominator this run got, rather than reps times conditions. A
        # panel that never held its shape was never a trial of the model.
        "panels_established": len(established),
        "not_established": len(trials) - len(established),
        "graded": len(graded),
        # The claim that justifies a second modality on this surface: when the
        # panel says the device did not get there, does the model say so.
        "detection_rate": _rate(sum(1 for t in non_arrival if t.model_correct), len(non_arrival)),
        "detection_trials": len(non_arrival),
        # And the subset that matters most, reported on its own because it is the
        # one where the DOM holds the requested number and the screen contradicts
        # it. Averaging it into the line above would let two easy conditions
        # carry it.
        "non_compliance_detection_rate": _rate(sum(1 for t in jammed if t.model_correct), len(jammed)),
        "non_compliance_trials": len(jammed),
        # And when the panel shows a device that did arrive, does it stay quiet.
        "false_alarm_rate": _rate(sum(1 for t in arrival if not t.model_correct), len(arrival)),
        "converged_trials": len(arrival),
        # Neither a detection nor a false alarm, so it is in neither numerator
        # and neither denominator. It is still a cost, so it is printed.
        "declined": sum(1 for t in established if t.model_says_met is None and t.source != "error"),
        # Every trial whose call produced nothing usable. The observer files a
        # reply it cannot parse under the same source as one that never
        # connected, so this counts both and each trial's note says which.
        "transport_failures": sum(1 for t in trials if t.source == "error"),
        "sources": _counts(t.source for t in trials if t.source),
        "by_condition": _by_condition(trials),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    """How many trials ended in each of the observer's own source labels.

    Printed alongside the rates because they say how the run went, not just how
    it scored: `low_confidence` twice and `vlm` nine times is a different run
    from eleven confident answers, and the rates alone look identical.
    """
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _by_condition(trials: list[RoomTrial]) -> dict[str, dict[str, Any]]:
    """Per condition, so a single weak one cannot hide inside an average."""
    result: dict[str, dict[str, Any]] = {}
    for condition in CONDITIONS:
        rows = [t for t in trials if t.condition == condition.name]
        if not rows:
            continue
        graded = [t for t in rows if t.graded]
        result[condition.name] = {
            "expected_answer": condition.expected_answer,
            "attempted": len(rows),
            "established": sum(1 for t in rows if t.established),
            "graded": len(graded),
            "answered_as_expected": sum(1 for t in graded if t.model_correct),
            "declined": sum(1 for t in rows if t.established and t.model_says_met is None and t.source != "error"),
            "transport_failures": sum(1 for t in rows if t.source == "error"),
        }
    return result


# --- driving the room and the page -------------------------------------------------


def _text(session: Any, selector: str) -> str:
    """The element's text, or empty when it cannot be read.

    Empty rather than raising: a reading that could not be taken makes a trial
    fail to establish, which is already an outcome this script reports, and that
    is a more useful answer than an exception halfway through a run.
    """
    try:
        return (session.text_content(selector) or "").strip()
    except Exception:
        return ""


def _read_panel(session: Any, condition: RoomCondition, baseline: str) -> PanelReading:
    return PanelReading(
        commanded=_text(session, condition.commanded_selector),
        measured=_text(session, condition.view.value_selector),
        baseline_measured=baseline,
    )


def _settled_baseline(session: Any, room: Room, condition: RoomCondition, deadline: float) -> PanelReading | None:
    """Wait until the panel is showing the room's own post-reset readings.

    Without this the baseline can be the previous trial's numbers: the page
    re-reads on its own schedule, so for up to one poll interval after a reset it
    is still displaying a room that no longer exists. A trial started from that
    reading would compare a fresh commanded value against a stale measurement,
    and a jammed blind sitting at the previous trial's position would look like
    it had moved.
    """
    while time.monotonic() < deadline:
        reading = _read_panel(session, condition, baseline="")
        # `proof_for` is how the panel formats a value, so this compares the page
        # against the room in the page's own units rather than in the room's.
        room_commanded = condition.view.proof_for(read_prop(room.wot, condition.thing, condition.commanded_property))
        if reading.commanded and reading.commanded == reading.measured == room_commanded:
            return PanelReading(reading.commanded, reading.measured, baseline_measured=reading.measured)
        time.sleep(PANEL_POLL_S)
    return None


def _capture(session: Any, condition: RoomCondition, before: PanelReading) -> tuple[PanelReading, bytes] | None:
    """Photograph the panel, and only keep the image if the panel held still.

    The page can poll while the region is being photographed, which would leave
    the image and the readings filed next to it describing two different moments.
    Evidence whose own label may disagree with it is not evidence, so that
    attempt is thrown away rather than graded.
    """
    image = session.screenshot_element(condition.view.region)
    if not image:
        return None
    after = _read_panel(session, condition, before.baseline_measured)
    return (after, image) if after == before else None


def establish(session: Any, room: Room, condition: RoomCondition) -> tuple[PanelReading, bytes] | None:
    """Drive the room until the panel is caught holding ``condition``'s shape."""
    commanded_shown = condition.view.proof_for(condition.value)
    for attempt in range(SETUP_ATTEMPTS):
        reset_room(room.control)
        if condition.fault:
            inject_fault(room.control, condition.thing, condition.fault)
        deadline = time.monotonic() + PANEL_WINDOW_S
        baseline = _settled_baseline(session, room, condition, deadline)
        if baseline is None:
            continue
        # Shift when the command is issued, a little further on each attempt.
        # Confirming the baseline means the page has just re-read, so a write
        # sent immediately puts a state that lives about a second entirely into
        # the gap before the next poll: with no offset the warming lamp was
        # missed on all eight attempts, which reads as a room that never warms
        # rather than as a page that never looked. Sweeping walks the state
        # across the poll instead of hardcoding the interval, which belongs to
        # the dashboard and is not this script's to depend on.
        time.sleep(attempt * PHASE_STEP_S)
        write_prop(room.wot, condition.thing, condition.commanded_property, condition.value)
        while time.monotonic() < deadline:
            reading = _read_panel(session, condition, baseline.measured)
            # The upper line has to be showing the value just written before the
            # relation between the lines means anything. Without this the very
            # first read, taken before the page next polls, is still the settled
            # baseline, whose two lines agree: `converged` then photographed the
            # panel from before its own command and graded the model against a
            # target the room had not been given.
            if reading.commanded == commanded_shown and condition.panel_shows(reading):
                captured = _capture(session, condition, reading)
                if captured is not None:
                    return captured
                break  # the panel moved under the camera; set the whole thing up again
            time.sleep(PANEL_POLL_S)
    return None


def run_trials(report: Report, *, room: Room, reps: int, headed: bool, client: Any) -> None:
    from src.perception.browser_session import BrowserSession

    # Two seconds, not the eight second default. Every selector below is on the
    # page whenever the page is up at all, so a long wait buys nothing and turns
    # a dashboard that failed to load into a run that appears to hang.
    session = BrowserSession.launch(room.dashboard, headless=not headed, action_timeout_ms=2000)
    try:
        for condition in CONDITIONS:
            for _ in range(reps):
                trial = RoomTrial(
                    condition=condition.name,
                    expected_answer=condition.expected_answer,
                    question=condition.question(),
                )
                established = establish(session, room, condition)
                if established is None:
                    trial.note = f"the panel never held this shape in {SETUP_ATTEMPTS} attempts; not graded"
                    report.trials.append(trial)
                    print(f"  {condition.name:<14} {trial.note}")
                    continue

                reading, image = established
                trial.established = True
                trial.panel_commanded = reading.commanded
                trial.panel_measured = reading.measured
                trial.room_commanded = read_prop(room.wot, condition.thing, condition.commanded_property)
                trial.room_measured = read_prop(room.wot, condition.thing, condition.measured_property)

                # One observer per trial. The shared cache keys on the image
                # bytes and the question, and two repetitions of a converged
                # panel are byte identical, so a single observer would hand back
                # the first answer and let repetitions that were never actually
                # taken look measured. The ceiling of one then says out loud what
                # a trial is allowed to cost.
                observer = VlmObserver(client=client, max_calls=1)
                judgement = observer.look(image, trial.question, region=condition.view.region)
                trial.model_says_met = judgement.answer if judgement.usable else None
                trial.confidence = judgement.confidence
                trial.evidence = judgement.evidence
                trial.source = judgement.source
                if judgement.error:
                    trial.note = judgement.error
                report.trials.append(trial)
                print(
                    f"  {condition.name:<14} panel {reading.commanded} / {reading.measured:<22} "
                    f"model {_answer_column(trial)}"
                )
    finally:
        session.close()
        # Leave nothing injected: the next thing anyone runs would inherit a
        # jammed motor and read it as a bug in their own code.
        reset_room(room.control)


# --- reporting ---------------------------------------------------------------------


def _shown(answer: bool | None) -> str:
    return "declined" if answer is None else ("yes" if answer else "no")


def _answer_column(trial: RoomTrial) -> str:
    """What the model contributed, distinguishing an abstention from a failure.

    Both leave ``model_says_met`` empty and neither counts towards a rate, but
    they are different facts about the run: one is the model being careful, the
    other is a reply that never arrived in a usable form. Printing both as
    "declined" would let the second hide inside the first.
    """
    if trial.source == "error":
        return "no reply"
    return _shown(trial.model_says_met)


def _pct(rate: float | None, total: int) -> str:
    """A percentage, or the reason there is not one."""
    return "not measured" if rate is None else f"{rate:.0%}  (n={total})"


def _previous_measurement(path: Path) -> dict[str, Any] | None:
    """The trials and summary already on disk, if a real run produced them.

    Carried forward with the timestamp they were taken at, so a run that could
    measure nothing does not erase evidence it did not gather. Writing a zeroed
    summary instead would put "detection 0%" into the field a reader cites, with
    no way to tell a model that caught nothing from a model that never ran.
    """
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    summary = previous.get("summary")
    if not isinstance(summary, dict) or not summary.get("graded"):
        return None
    return {
        "trials": previous.get("trials", []),
        "summary": {**summary, "carried_forward_from": str(previous.get("at", "an earlier run"))},
    }


def print_summary(report: Report) -> None:
    summary = report.summary()
    print(f"\n  {'condition':<14} {'want':>8} {'panel: commanded / measured':<32} {'model':>8} {'conf':>5}  evidence")
    print(f"  {'-' * 106}")
    for trial in report.trials:
        panel = f"{trial.panel_commanded} / {trial.panel_measured}" if trial.established else "(never established)"
        print(
            f"  {trial.condition:<14} {_shown(trial.expected_answer):>8} {panel[:32]:<32} "
            f"{_answer_column(trial):>8} {trial.confidence:>5.2f}  {(trial.evidence or trial.note)[:34]}"
        )

    print()
    print(
        f"  panels established                        : {summary['panels_established']}/{summary['trials_attempted']}"
    )
    print(f"  graded (panel held its shape and answered): {summary['graded']}")
    print()
    print(
        f"  detection, panel does not show arrival    : {_pct(summary['detection_rate'], summary['detection_trials'])}"
    )
    print(
        f"    of which motor_jam, the one that matters: "
        f"{_pct(summary['non_compliance_detection_rate'], summary['non_compliance_trials'])}"
    )
    print(
        f"  false alarm, panel shows an arrived device: {_pct(summary['false_alarm_rate'], summary['converged_trials'])}"
    )
    print()
    print(f"  declined, the model would not commit      : {summary['declined']}")
    # Named for what the observer actually puts in this bucket. It classifies a
    # reply it cannot parse the same way as a call that never connected, so
    # calling the line "transport failures" on its own would claim more about the
    # network than the data supports. Each trial's own note says which it was.
    print(f"  no usable reply (transport or bad JSON)   : {summary['transport_failures']}")
    print(f"  panels that never held their shape        : {summary['not_established']}")
    if summary["sources"]:
        print(f"  answer sources                            : {summary['sources']}")

    print()
    print("  per condition (answered as expected / graded):")
    for name, stats in summary["by_condition"].items():
        print(
            f"    {name:<14} {stats['answered_as_expected']}/{stats['graded']}"
            f"   established {stats['established']}/{stats['attempted']}"
            f"   declined {stats['declined']}   no reply {stats['transport_failures']}"
        )

    print()
    print("  what the model said it saw (first graded repetition of each condition):")
    for condition in CONDITIONS:
        first = next((t for t in report.trials if t.condition == condition.name and t.graded), None)
        if first is None:
            print(f"    {condition.name:<14} nothing graded")
            continue
        print(f"    {condition.name:<14} panel read {first.panel_commanded} / {first.panel_measured}")
        print(f"    {'':<14} asked      {first.question}")
        print(f"    {'':<14} answered   {_shown(first.model_says_met)} at {first.confidence:.2f}: {first.evidence}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reps", type=int, default=3, help="Repetitions per condition.")
    parser.add_argument("--wot", default=DEFAULT_WOT, help="Base URL of the WoT servient.")
    parser.add_argument("--control", default=DEFAULT_CONTROL, help="Base URL of the failure control plane.")
    parser.add_argument("--dashboard", default=DEFAULT_DASHBOARD, help="Dashboard URL to photograph.")
    parser.add_argument("--headed", action="store_true", help="Show the browser instead of running it headless.")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    room = Room(wot=args.wot, control=args.control, dashboard=args.dashboard)
    out = Path("artifacts") / "room_vision"
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "report.json"

    print(f"\n{_LINE}\n  CAN THE MODEL READ THE ROOM'S PANELS, OR ONLY WHAT THE ROOM WAS TOLD?\n{_LINE}")

    client = available_vision_client()
    report = Report()
    payload: dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "reps_requested": args.reps,
        "model": getattr(client, "name", "") if client is not None else "",
        "dashboard": room.dashboard,
    }

    if client is None:
        print("\n  no vision model is configured, so nothing here was measured.")
        print("  set DASHSCOPE_API_KEY (or OPENAI_API_KEY, ANTHROPIC_API_KEY) in .env.local.")
        carried = _previous_measurement(destination)
        if carried is not None:
            print(f"  keeping the measurement from {carried['summary']['carried_forward_from']}, which was a real run.")
            payload.update(carried)
        else:
            payload["trials"] = []
            payload["summary"] = {"not_measured": "no vision model was configured when this ran"}
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\n  artifact : {destination.as_posix()}\n{_LINE}\n")
        return 0

    if not reset_room(room.control):
        print(f"\n  the control plane at {room.control} is not answering.")
        print("  start it with:  docker compose -f env/docker-compose.yml up -d\n")
        return 2

    print(f"  model     : {getattr(client, 'name', 'unknown')}")
    print(f"  dashboard : {room.dashboard}")
    print(f"  trials    : {args.reps} repetitions of {len(CONDITIONS)} conditions, one paid call each")
    print("  note      : the room runs at 30x real time and models timing and compliance,")
    print("              not thermodynamics or hardware.\n")

    run_trials(report, room=room, reps=args.reps, headed=args.headed, client=client)
    print_summary(report)

    summary = report.summary()
    if summary["graded"]:
        payload["trials"] = [t.to_dict() for t in report.trials]
        payload["summary"] = summary
    else:
        # Every trial failed to establish or came back unusable. That is a real
        # outcome and it is printed above, but it is not a measurement of the
        # model, so it must not overwrite one.
        carried = _previous_measurement(destination)
        print("\n  nothing was graded this run.")
        if carried is not None:
            print(f"  keeping the measurement from {carried['summary']['carried_forward_from']}, which was a real run.")
            payload["attempted_this_run"] = [t.to_dict() for t in report.trials]
            payload.update(carried)
        else:
            payload["trials"] = [t.to_dict() for t in report.trials]
            payload["summary"] = {"not_measured": "no trial produced both a panel and an answer"}

    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  artifact : {destination.as_posix()}\n{_LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
