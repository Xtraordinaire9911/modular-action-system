"""One declaration per demo, and a runtime answer to "can it run here?".

Demos had accumulated across ``run_demo.py``, six ``scripts/run_*.py`` entry
points and several ``src.pipeline`` flags. Nothing enumerated them, so the only
way to find out what could be shown on a given machine was to try each one and
read the traceback. That is how a demo ends up missing from a meeting.

Two properties this file is designed around:

*Extensible* — adding a demo means appending one :class:`Demo` entry. No runner
code changes, no README edit required for it to become discoverable.

*Backward compatible* — nothing here modifies or wraps the existing scripts.
They keep their own flags and stay runnable directly; the registry only points
at them. A demo whose script is absent from the current checkout is reported as
missing rather than raising, so the registry stays valid on any branch and while
a feature is still in review.
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Demo:
    """A runnable demonstration.

    ``command`` is the argv that follows the Python interpreter, so a demo can
    be a script (``["scripts/run_fancy_demo.py"]``) or a module invocation
    (``["-m", "src.pipeline", "--live-demo"]``) without special-casing.
    """

    name: str
    title: str
    summary: str
    command: tuple[str, ...]
    requires: tuple[str, ...] = ()
    headed_args: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()
    duration_hint: str = ""

    @property
    def script_path(self) -> Path | None:
        """The file this demo runs, when it runs a script rather than a module."""
        first = self.command[0]
        return None if first.startswith("-") else REPO_ROOT / first

    @property
    def exists_here(self) -> bool:
        path = self.script_path
        return True if path is None else path.is_file()


# ── capabilities ────────────────────────────────────────────────────────────────
# Each demo declares what it needs; the checks live here so the CLI and the tests
# agree on what "available" means.


def _has_browser() -> tuple[bool, str]:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "playwright not installed - run: uv pip install -e '.[dev]'"
    # Playwright downloads browsers outside site-packages; look for the cache
    # rather than launching one, so the check stays fast and side-effect free.
    default_cache = {
        "win32": Path(os.environ.get("LOCALAPPDATA", "~")) / "ms-playwright",
        "darwin": Path("~/Library/Caches/ms-playwright"),
    }.get(sys.platform, Path("~/.cache/ms-playwright"))
    cache = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or default_cache).expanduser()
    if cache.is_dir() and any(cache.glob("chromium*")):
        return True, "chromium installed"
    return False, "chromium missing - run: uv run playwright install chromium"


def _has_miniwob() -> tuple[bool, str]:
    path = REPO_ROOT / ".external_envs" / "miniwob-plusplus" / "miniwob" / "html"
    if path.is_dir():
        return True, "MiniWoB++ clone present"
    return False, "clone missing - see env/RUNBOOK_external_envs.md A2"


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _has_smart_room() -> tuple[bool, str]:
    dashboard, wot = _port_open(3000), _port_open(8080)
    if dashboard and wot:
        return True, "dashboard and WoT servient reachable"
    down = [n for n, ok in (("dashboard :3000", dashboard), ("WoT :8080", wot)) if not ok]
    return False, f"{', '.join(down)} unreachable - docker compose -f env/docker-compose.yml up"


CAPABILITIES: dict[str, Callable[[], tuple[bool, str]]] = {
    "browser": _has_browser,
    "miniwob": _has_miniwob,
    "smart_room": _has_smart_room,
}


def check_capability(name: str) -> tuple[bool, str]:
    probe = CAPABILITIES.get(name)
    if probe is None:
        return False, f"unknown capability {name!r}"
    return probe()


def capability_report() -> dict[str, tuple[bool, str]]:
    return {name: probe() for name, probe in CAPABILITIES.items()}


# ── the registry ────────────────────────────────────────────────────────────────
# Ordered from "works anywhere" to "needs the most setup", which is also a
# sensible order to show them in.

DEMOS: list[Demo] = [
    Demo(
        name="agent-loop",
        title="The narrated agent loop, with realistic faults",
        summary="Seven scenes over shop, forum and a WoT device; six inject a different real-world "
        "fault and the agent diagnoses each from measurements before choosing a recovery tier.",
        command=("scripts/run_agent_loop_demo.py",),
        requires=("browser",),
        headed_args=(),  # headed is the default here; --headless is the opt-out
        duration_hint="~5min",
    ),
    Demo(
        name="llm-loop",
        title="The same loop, with and without a model",
        summary="Four scenes that put the rule-based path beside the model path on the same "
        "sentence, in the smart room. One scene leaves the browser entirely and writes to a "
        "device resolved from the room's own Thing Descriptions; the last one ends with a "
        "dashboard whose confirmation is in the DOM and painted over on screen, which only the "
        "vision model catches. Needs the smart room up and a configured API key.",
        command=("scripts/run_llm_demo.py",),
        requires=("browser", "smart_room"),
        headed_args=(),  # headed is the default here; --headless is the opt-out
        duration_hint="~2min",
    ),
    Demo(
        name="intent-runtime",
        title="An utterance drives the production runtime",
        summary="A sentence becomes a GoalSpec and is executed by RuntimeEpisodeRunner and the "
        "ContinuousInteractionManager on a live page, with the goal verified by re-observation.",
        command=("scripts/run_intent_episode.py",),
        requires=("browser",),
        headed_args=("--headed",),
        duration_hint="~20s",
    ),
    Demo(
        name="intent-cross-env",
        title="M1 cross-environment generalisation, agent-driven",
        summary="Seven spoken requests over a shop and a forum, each executed by the real runtime "
        "and verified by re-observation, reported as the M1 per-environment table.",
        command=("scripts/run_intent_episode.py", "--suite"),
        requires=("browser",),
        headed_args=("--headed",),
        duration_hint="~40s",
    ),
    Demo(
        name="model-value",
        title="Do the models earn their place?",
        summary="Measures whether the intent model understands what the rules cannot, and whether the vision "
        "model catches a false success the DOM confirms. Needs a configured API key.",
        # Four repetitions, because that is the sample size the numbers quoted in
        # the README and in docs_setup/VLM_SETUP.md come from. Running the demo
        # at a different --reps would produce a table nobody could match to the
        # documented one.
        command=("scripts/eval_model_value.py", "--reps", "4"),
        requires=("browser",),
        duration_hint="~3min",
    ),
    Demo(
        name="room-prepared",
        title="One sentence prepares a room, and every property is checked",
        summary='Discovers the Thing Descriptions at runtime, resolves "prepare the room" to four '
        "writable properties, writes each one and reads each one back. --ignore drops a write to show "
        "the read-back catching it.",
        command=("scripts/run_room_prepared.py",),
        requires=("smart_room",),
        duration_hint="~15s",
    ),
    Demo(
        name="joint-pipeline",
        title="One sentence, one episode, three components",
        summary="A single episode where every stage names whose component owns it: intent, WoT reach "
        "and read-back verification from the action system, a real fault injected into the room, "
        "recovery asked for through the PlannerPort the recovery component owns, and the room "
        "restored at the end. A jammed motor is not recoverable, so the run ends in a handover that "
        "is reported as handled correctly and never counted as a success. Where a stage stands in for "
        "code that is not wired in yet, the run says so on the line.",
        command=("scripts/run_joint_pipeline.py",),
        requires=("smart_room",),
        duration_hint="~30s",
    ),
    Demo(
        name="wot-conformance",
        title="Standard W3C WoT Thing Descriptions, and how the agent finds them",
        summary="Reads the running room and prints four things: what the Thing Directory returns, the "
        "TD's @context and security definitions, one property's complete forms array with its op values "
        "and readOnly flag, and the binding table entry beside the href resolved from it. The entry names "
        "a kind of Thing and a property and contains no URL, which is the difference between discovery "
        "and a typed-in endpoint. Read only.",
        command=("scripts/show_wot_conformance.py",),
        requires=("smart_room",),
        duration_hint="~5s",
    ),
    Demo(
        name="commanded-vs-measured",
        title="The command succeeded and the room did not comply",
        summary="Jams a blinds motor, then shows three verification strategies disagreeing about the same "
        "write: transport says 204, the commanded property reads back exactly what was asked, and the "
        "device's own measurement never moved. No model and no API key, so it cannot fail on the network. "
        "Opens the dashboard when a browser is available; --headless prints the same readings without one, "
        "which is why only the room is listed as required.",
        command=("scripts/run_commanded_vs_measured.py",),
        requires=("smart_room",),
        duration_hint="~50s",
    ),
    Demo(
        name="offline",
        title="Deterministic offline trace",
        summary="Runtime trace, postcondition checks and recovery metrics. No browser, no Docker.",
        command=("run_demo.py",),
        duration_hint="~5s",
    ),
    Demo(
        name="visual-grounding",
        title="Visual grounding smoke trace",
        summary="Screenshot in, geometry measured in the browser, Set-of-Marks out, click by mark.",
        command=("scripts/run_visual_grounding_smoke.py",),
        requires=("browser",),
        headed_args=("--headed",),
        duration_hint="~15s",
    ),
    Demo(
        name="mock-envs",
        title="WebArena-style mock environments",
        summary="Six tasks across shopping, email and forum surfaces with a success table.",
        command=("scripts/run_fancy_demo.py", "--skip-miniwob"),
        requires=("browser",),
        headed_args=("--headed",),
        extra_args=("--step-delay", "1.2"),
        duration_hint="~1min",
    ),
    Demo(
        name="cross-env",
        title="Cross-environment suite (academic + industrial)",
        summary="MiniWoB++ tasks plus the mock environments, with the M1 generalisation table.",
        command=("scripts/run_fancy_demo.py",),
        requires=("browser", "miniwob"),
        headed_args=("--headed",),
        extra_args=("--step-delay", "1.2"),
        duration_hint="~2min",
    ),
    Demo(
        name="miniwob",
        title="MiniWoB++ curated suite",
        summary="Six MiniWoB++ tasks only, with per-task reward.",
        command=("scripts/run_miniwob_demo.py",),
        requires=("browser", "miniwob"),
        headed_args=("--headed",),
        extra_args=("--step-delay", "1.2"),
        duration_hint="~1min",
    ),
    Demo(
        name="live-runtime",
        title="Live runtime tracer bullet",
        summary="Observe-plan-act-verify-recover against the running smart-room environment. "
        "Six episodes, headless by default; add --headed to watch the dashboard being driven.",
        command=("-m", "src.pipeline", "--live-demo"),
        requires=("smart_room",),
        headed_args=("--headed",),
        # Timed three times against the running room: 16, 17 and 20 seconds. The
        # previous "~2min" was a guess, and it is why a 14-second recording of this
        # demo looked like a truncated one.
        duration_hint="~20s",
    ),
    Demo(
        name="adaptation",
        title="Adaptation and policy proposal",
        summary="Failure boundary classification through to a review-gated policy proposal.",
        command=("scripts/run_adaptation_demo.py",),
        duration_hint="~10s",
    ),
]


class DemoStatus:
    READY = "ready"
    NOT_IN_CHECKOUT = "not-in-checkout"
    MISSING_CAPABILITY = "needs-setup"


@dataclass
class Status:
    state: str
    detail: str = ""
    missing: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.state == DemoStatus.READY


def status_of(demo: Demo) -> Status:
    """Whether this demo can run right now, and if not, why not."""
    if not demo.exists_here:
        return Status(DemoStatus.NOT_IN_CHECKOUT, f"{demo.command[0]} is not in this checkout")
    missing: list[str] = []
    details: list[str] = []
    for capability in demo.requires:
        ok, detail = check_capability(capability)
        if not ok:
            missing.append(capability)
            details.append(detail)
    if missing:
        return Status(DemoStatus.MISSING_CAPABILITY, "; ".join(details), missing)
    return Status(DemoStatus.READY, demo.duration_hint)


def find(name: str) -> Demo | None:
    return next((d for d in DEMOS if d.name == name), None)


def runnable(demos: Iterable[Demo] | None = None) -> list[Demo]:
    return [d for d in (demos if demos is not None else DEMOS) if status_of(d).ready]


def build_argv(demo: Demo, *, headed: bool = False, extra: Iterable[str] = ()) -> list[str]:
    """Full argv for this demo, interpreter included."""
    argv = [sys.executable, *demo.command, *demo.extra_args]
    if headed:
        argv.extend(demo.headed_args)
    argv.extend(extra)
    return argv
