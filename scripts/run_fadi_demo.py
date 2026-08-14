"""Short weekly demo: GoalSpec -> Skill -> primitives -> supervised takeover.

Live, visual walkthrough (requires the Docker smart-room environment):

    uv run python scripts/run_fadi_demo.py --headed

Deterministic rehearsal without Docker or Chromium:

    uv run python scripts/run_fadi_demo.py --dry-run

The script is intentionally only setup and operator UI. Planning, primitive
execution, pause/resume, fresh observation, and cleanup remain owned by the
existing ContinuousInteractionManager and Project PiP isolation provider.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adaptation.trace_ledger import TraceLedger  # noqa: E402
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall  # noqa: E402
from src.isolation.episode import BrowserWotIsolationProvider  # noqa: E402
from src.planner.goal_skill_selector import GoalSkillSelection, GoalSkillSelector  # noqa: E402
from src.runtime.cognitive_map import CognitiveMap  # noqa: E402
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult  # noqa: E402
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger  # noqa: E402
from src.runtime.goal_spec import GoalSpec  # noqa: E402
from src.runtime.intervention import (  # noqa: E402
    InMemoryInterventionBroker,
    InterventionAction,
    InterventionDecision,
    InterventionLedger,
    InterventionRequest,
)
from src.runtime.live_environment import (  # noqa: E402
    AffordanceSemanticBinding,
    DomStateProbe,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SmartRoomControlClient,
    SmartRoomLiveEnvironment,
    ThreadedBrowserSession,
    ThreadedDomEffector,
)
from src.runtime.live_observation import (  # noqa: E402
    LiveRuntimeObservation,
    bind_live_observation_to_request,
    observation_from_live_sources,
)
from src.skill_library import load_skill_library  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_TITLE = "Supervised takeover / isolation toward PiP"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "fadi_weekly_demo" / "episode.json"


@dataclass(frozen=True)
class DemoPaths:
    evidence: Path
    transitions: Path
    interventions: Path
    failures: Path


@dataclass
class DemoRun:
    goal: GoalSpec
    selection: GoalSkillSelection
    result: RuntimeStepResult
    transitions: TransitionLedger
    interventions: InterventionLedger
    failures: TraceLedger
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    browser_context_generation: int
    isolation_active_after: bool
    executor_calls: list[SkillCall]
    mode: str
    isolation_events: list[str]
    screenshot_paths: list[str]


def build_goal(room: str = "A", time_slot: str = "14:00") -> GoalSpec:
    """The only task input used by the demo."""

    return GoalSpec(
        goal_id="confirm_booking",
        goal_state="booking.confirmed == true",
        parameters={"room": room, "time": time_slot},
        source="demo",
        description=f"Confirm Room {room} for {time_slot}",
        safety_constraints=["A human must confirm the final booking action"],
        success_evidence=["Fresh DOM observation says booking.confirmed == true"],
    )


def select_skill(goal: GoalSpec) -> GoalSkillSelection:
    library = load_skill_library(REPO_ROOT / "config" / "skills_seed.json")
    return GoalSkillSelector(library).select(goal)


def booking_probes() -> list[DomStateProbe]:
    """Independent state reads used to verify the effect after each action."""

    return [
        DomStateProbe(
            "[data-testid='booking-status']",
            "booking",
            "confirmed",
            value_type="boolean",
            true_pattern=r"^booked:",
            false_pattern=r"^not booked$",
        ),
        DomStateProbe("[data-testid='room-input']", "booking", "room", extraction="value"),
        DomStateProbe("[data-testid='time-input']", "booking", "time", extraction="value"),
    ]


def booking_bindings() -> list[AffordanceSemanticBinding]:
    """Describe meanings, not an authored click sequence.

    The runtime discovers the controls and generates the order of primitive
    actions. Only the final commit control is promoted to high risk, which
    makes the Tier-4 stop deterministic for this supervised demonstration.
    """

    return [
        AffordanceSemanticBinding(
            "DOM",
            entity_id="booking",
            state_attribute="room",
            selector="[data-testid='room-input']",
            binds_parameter="room",
            stable_key="booking.room",
            idempotent=True,
        ),
        AffordanceSemanticBinding(
            "DOM",
            entity_id="booking",
            state_attribute="time",
            selector="[data-testid='time-input']",
            binds_parameter="time",
            stable_key="booking.time",
            idempotent=True,
        ),
        AffordanceSemanticBinding(
            "DOM",
            entity_id="booking",
            selector="[data-testid='book-room-button']",
            completion_for="confirm_booking",
            achieves="booking.confirmed == true",
            stable_key="booking.confirm",
            safety_level="high",
        ),
    ]


def _episode_policy() -> EpisodePolicy:
    return EpisodePolicy(
        max_steps=8,
        deadline_s=120.0,
        max_retry_attempts=1,
        max_attempts_per_backend=4,
        require_fresh_observation=True,
    )


def _prepare_paths(evidence_path: str | Path) -> DemoPaths:
    evidence = Path(evidence_path)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    paths = DemoPaths(
        evidence=evidence,
        transitions=evidence.with_name("transition_ledger.jsonl"),
        interventions=evidence.with_name("intervention_ledger.jsonl"),
        failures=evidence.with_name("failure_ledger.jsonl"),
    )
    for generated in (paths.transitions, paths.interventions, paths.failures):
        generated.unlink(missing_ok=True)
    return paths


def _print_goal_and_skill(goal: GoalSpec, selection: GoalSkillSelection) -> None:
    skill = selection.skill_tuple
    print(f"\n{DEMO_TITLE}")
    print("=" * len(DEMO_TITLE))
    print(f"1. GoalSpec received : {goal.goal_id} {goal.parameters}")
    print(f"2. Skill selected    : {skill.skill_id}")
    print(f"   parameters        : {skill.parameters_schema}")
    print(f"   preconditions     : {[item.predicate for item in skill.preconditions]}")
    print(f"   postconditions    : {[item.predicate for item in skill.postconditions]}")
    print("3. Runtime starts an isolated episode and plans from live affordances.")


async def _operator_loop(
    broker: InMemoryInterventionBroker,
    runtime_task: asyncio.Task[RuntimeStepResult],
    *,
    mode: str,
) -> None:
    """Resolve runtime-owned pauses; never issue an agent action here."""

    while not runtime_task.done():
        try:
            request = await broker.next_request(timeout_s=0.2)
        except TimeoutError:
            continue

        _print_pause(request)
        if mode == "auto_approve":
            print("   Auto mode: approving the exact pending action.\n")
            decision = InterventionDecision(
                InterventionAction.APPROVE,
                actor="demo_operator",
                note="Noninteractive rehearsal approved the protected action",
            )
        elif mode == "simulated_takeover":
            print("   Dry-run mode: simulating that the human completed the booking.\n")
            decision = InterventionDecision(
                InterventionAction.RESUME,
                actor="fadi",
                note="Dry-run human completed Book Room",
                correction_applied=True,
            )
        else:
            decision = await _interactive_decision()
        broker.resolve(request.intervention_id, decision)


def _print_pause(request: InterventionRequest) -> None:
    print("\n4. TIER-4 PAUSE")
    print(f"   reason            : {request.reason}")
    print(f"   pending action    : {request.pending_action_fingerprint}")
    print("   The agent is waiting. It cannot click while the human has control.")


async def _interactive_decision() -> InterventionDecision:
    while True:
        try:
            choice = (
                (
                    await asyncio.to_thread(
                        input,
                        "\nChoose [a] approve, [t] take control, [r] reject, or [c] cancel: ",
                    )
                )
                .strip()
                .lower()
            )
        except EOFError:
            return InterventionDecision(
                InterventionAction.CANCEL,
                actor="system",
                note="No interactive terminal was available",
            )
        if choice == "a":
            return InterventionDecision(
                InterventionAction.APPROVE,
                actor="fadi",
                note="Supervisor approved the pending booking action",
            )
        if choice == "t":
            print("\nTake control in the browser and click 'Book Room'.")
            try:
                await asyncio.to_thread(input, "When the booking is visible, press Enter here to return control: ")
            except EOFError:
                return InterventionDecision(
                    InterventionAction.CANCEL,
                    actor="system",
                    note="Human takeover could not be completed without an interactive terminal",
                )
            return InterventionDecision(
                InterventionAction.RESUME,
                actor="fadi",
                note="Human completed Book Room in the visible browser",
                correction_applied=True,
            )
        if choice == "r":
            return InterventionDecision(
                InterventionAction.REJECT,
                actor="fadi",
                note="Supervisor rejected the protected booking action",
            )
        if choice == "c":
            return InterventionDecision(
                InterventionAction.CANCEL,
                actor="fadi",
                note="Supervisor cancelled the episode",
            )
        print("Please enter a, t, r, or c.")


async def run_live_demo(args: argparse.Namespace, paths: DemoPaths) -> DemoRun:
    goal = build_goal(args.room, args.time)
    selection = select_skill(goal)
    _print_goal_and_skill(goal, selection)

    config = LiveEnvironmentConfig(
        dashboard_url=args.dashboard_url,
        thing_directory_url=args.thing_directory_url,
        wot_public_base_url=args.wot_base_url,
        control_url=args.control_url,
        output_dir=paths.evidence.parent,
    )
    session = ThreadedBrowserSession(config.dashboard_url, headless=not args.headed)
    control = SmartRoomControlClient(config.control_url)
    state_before = await control.state()
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        dom_state_probes=booking_probes(),
        semantic_bindings=booking_bindings(),
        include_wot_state=False,
        allowed_affordance_sources={"DOM"},
    )
    dom_executor = _RecordingExecutor(RuntimeAffordanceExecutor("dom", environment, ThreadedDomEffector(session)))
    transitions = TransitionLedger(paths.transitions)
    interventions = InterventionLedger(paths.interventions)
    failures = TraceLedger()
    broker = InMemoryInterventionBroker(interventions)
    isolation = BrowserWotIsolationProvider(session, control)
    manager = ContinuousInteractionManager(
        {selection.skill_tuple.skill_id: selection.skill_tuple},
        {"dom": dom_executor},
        CognitiveMap(task_id="fadi-confirm-booking"),
        observation_provider=environment,
        episode_policy=_episode_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        isolation_provider=isolation,
        intervention_broker=broker,
        intervention_ledger=interventions,
    )

    operator_mode = "auto_approve" if args.auto_approve else "interactive"
    try:
        runtime_task = asyncio.create_task(
            manager.run_isolated_goal(
                goal_id=selection.skill_call.skill_id,
                goal_state=goal.goal_state,
                parameters=dict(selection.skill_call.params),
                goal_spec=goal,
            )
        )
        operator_task = asyncio.create_task(_operator_loop(broker, runtime_task, mode=operator_mode))
        try:
            result = await runtime_task
        finally:
            await operator_task
    finally:
        await session.close()

    state_after = await control.state()
    failures.write_jsonl(paths.failures)
    screenshot_dir = paths.evidence.parent / "screenshots" / "fadi-confirm-booking" / result.episode_id
    screenshots = sorted(str(path) for path in screenshot_dir.glob("*.png"))
    return DemoRun(
        goal=goal,
        selection=selection,
        result=result,
        transitions=transitions,
        interventions=interventions,
        failures=failures,
        state_before=state_before,
        state_after=state_after,
        browser_context_generation=session.context_generation,
        isolation_active_after=isolation.active_session is not None,
        executor_calls=dom_executor.calls,
        mode="live_auto_approve" if args.auto_approve else "live_interactive",
        isolation_events=[],
        screenshot_paths=screenshots,
    )


async def run_dry_demo(paths: DemoPaths, *, room: str = "A", time_slot: str = "14:00") -> DemoRun:
    """Rehearse the same runtime contracts without external services."""

    goal = build_goal(room, time_slot)
    selection = select_skill(goal)
    _print_goal_and_skill(goal, selection)

    observations = _dry_observations()
    provider = _DryObservationProvider(observations)
    isolation_events: list[str] = []
    browser = _DryBrowser(isolation_events)
    control = _DryControl(isolation_events)
    state_before = copy.deepcopy(control.state)
    executor = _DryExecutor()
    transitions = TransitionLedger(paths.transitions)
    interventions = InterventionLedger(paths.interventions)
    failures = TraceLedger()
    broker = InMemoryInterventionBroker(interventions)
    isolation = BrowserWotIsolationProvider(browser, control)
    manager = ContinuousInteractionManager(
        {selection.skill_tuple.skill_id: selection.skill_tuple},
        {"dom": executor},
        CognitiveMap(task_id="fadi-confirm-booking-dry-run"),
        observation_provider=provider,
        episode_policy=_episode_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        isolation_provider=isolation,
        intervention_broker=broker,
        intervention_ledger=interventions,
    )

    runtime_task = asyncio.create_task(
        manager.run_isolated_goal(
            goal_id=selection.skill_call.skill_id,
            goal_state=goal.goal_state,
            parameters=dict(selection.skill_call.params),
            goal_spec=goal,
        )
    )
    operator_task = asyncio.create_task(_operator_loop(broker, runtime_task, mode="simulated_takeover"))
    try:
        result = await runtime_task
    finally:
        await operator_task
    failures.write_jsonl(paths.failures)

    return DemoRun(
        goal=goal,
        selection=selection,
        result=result,
        transitions=transitions,
        interventions=interventions,
        failures=failures,
        state_before=state_before,
        state_after=copy.deepcopy(control.state),
        browser_context_generation=browser.context_generation,
        isolation_active_after=isolation.active_session is not None,
        executor_calls=executor.calls,
        mode="dry_run_simulated_takeover",
        isolation_events=isolation_events,
        screenshot_paths=[],
    )


class _RecordingExecutor:
    def __init__(self, delegate: RuntimeAffordanceExecutor) -> None:
        self.delegate = delegate
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        self.calls.append(skill_call)
        return await self.delegate.execute(skill_call, observation)


class _DryBrowser:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.context_generation = 0

    async def recreate(self) -> None:
        self.context_generation += 1
        self.events.append("browser:recreate")

    async def stop(self) -> None:
        self.events.append("browser:stop")


class _DryControl:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.state: dict[str, Any] = {
            "state": {"thermostat": {"targetTemperature": 19}},
            "faults": {"thermostat": {"type": "timeout"}},
        }
        self._checkpoint: dict[str, Any] | None = None
        self.lease_id = ""

    async def acquire_lease(self, episode_id: str) -> dict[str, Any]:
        self.events.append("wot:acquire")
        self._checkpoint = copy.deepcopy(self.state)
        self.lease_id = f"lease:{episode_id}"
        self.state = {"state": {"thermostat": {"targetTemperature": 20}}, "faults": {}}
        return {"lease_id": self.lease_id, "checkpoint": copy.deepcopy(self._checkpoint)}

    async def restore_lease(self) -> dict[str, Any]:
        self.events.append("wot:restore")
        assert self._checkpoint is not None
        self.state = copy.deepcopy(self._checkpoint)
        return copy.deepcopy(self.state)

    async def release_lease(self) -> dict[str, Any]:
        self.events.append("wot:release")
        assert self._checkpoint is not None
        self.state = copy.deepcopy(self._checkpoint)
        self._checkpoint = None
        self.lease_id = ""
        return {"status": "released"}


class _DryObservationProvider:
    def __init__(self, observations: list[LiveRuntimeObservation]) -> None:
        self.observations = list(observations)
        self.requests: list[ObservationRequest] = []

    def begin_episode(self, episode_id: str) -> None:
        _ = episode_id

    async def observe(self, request: ObservationRequest) -> LiveRuntimeObservation:
        self.requests.append(request)
        if not self.observations:
            raise RuntimeError(f"dry-run observation exhausted at {request.reason}")
        return bind_live_observation_to_request(self.observations.pop(0), request_id=request.request_id)


class _DryExecutor:
    def __init__(self) -> None:
        self.calls: list[SkillCall] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        self.calls.append(skill_call)
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="dom",
            success=True,
            latency_ms=1.0,
            confidence=1.0,
            observation_source="dom",
            metadata={"dry_run": True, "affordance_id": skill_call.params.get("affordance_id", "")},
        )


def _dry_observations() -> list[LiveRuntimeObservation]:
    return [
        _dry_booking_observation(confirmed=False),
        _dry_booking_observation(confirmed=False, room="A"),
        _dry_booking_observation(confirmed=False, room="A", time_slot="14:00"),
        # The simulated operator completed the final button before RESUME.
        _dry_booking_observation(confirmed=True, room="A", time_slot="14:00"),
    ]


def _dry_booking_observation(
    *,
    confirmed: bool,
    room: str = "",
    time_slot: str = "",
) -> LiveRuntimeObservation:
    affordances = [
        Affordance(
            id="dom_room",
            source="DOM",
            type="input",
            label="Room",
            action="type",
            locator={
                "entity_id": "booking",
                "binds_parameter": "room",
                "stable_key": "booking.room",
                "idempotent": True,
            },
            confidence=0.99,
        ),
        Affordance(
            id="dom_time",
            source="DOM",
            type="input",
            label="Time",
            action="type",
            locator={
                "entity_id": "booking",
                "binds_parameter": "time",
                "stable_key": "booking.time",
                "idempotent": True,
            },
            confidence=0.99,
        ),
        Affordance(
            id="dom_confirm",
            source="DOM",
            type="button",
            label="Book Room",
            action="click",
            locator={
                "entity_id": "booking",
                "completion_for": "confirm_booking",
                "achieves": "booking.confirmed == true",
                "stable_key": "booking.confirm",
            },
            confidence=0.99,
            safety_level="high",
        ),
    ]
    return observation_from_live_sources(
        page_state={"booking": {"confirmed": confirmed, "room": room, "time": time_slot}},
        wot_affordances=affordances,
    )


def build_evidence(run: DemoRun) -> dict[str, Any]:
    episode_id = run.result.episode_id
    return {
        "title": DEMO_TITLE,
        "scope_note": "Project-level browser/WoT isolation; not the UFO2 Windows RDP child desktop",
        "mode": run.mode,
        "goal_spec": asdict(run.goal),
        "selected_skill": asdict(run.selection.skill_tuple),
        "instantiated_skill_call": asdict(run.selection.skill_call),
        "runtime_skill_selection": run.result.goal_skill_selection,
        "runtime_evidence_trace": run.result.evidence_trace,
        "generated_primitive_plan": run.result.primitive_plan,
        "agent_executor_calls": [asdict(call) for call in run.executor_calls],
        "transitions": [asdict(record) for record in run.transitions.for_episode(episode_id)],
        "human_interventions": [asdict(record) for record in run.interventions.for_episode(episode_id)],
        "failure_events": [asdict(event) for event in run.failures.events if event.episode_id == episode_id],
        "result": {
            "episode_id": episode_id,
            "state": run.result.state.value,
            "outcome": run.result.outcome.value,
            "reason": run.result.reason,
            "failure_type": run.result.failure_type,
            "attempts": run.result.attempts,
            "final_outcome_verified": run.result.final_outcome_verified,
            "intervention_replan_count": sum(
                1 for record in run.interventions.for_episode(episode_id) if record.replanned
            ),
        },
        "isolation": {
            "browser_context_generation": run.browser_context_generation,
            "provider_active_after_run": run.isolation_active_after,
            "room_state_before": run.state_before,
            "room_state_after": run.state_after,
            "room_state_restored": run.state_before == run.state_after,
            "events": run.isolation_events,
        },
        "screenshots": run.screenshot_paths,
    }


def write_evidence(run: DemoRun, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_evidence(run), indent=2, sort_keys=True, default=_json_default) + "\n")
    return path


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)  # type: ignore[arg-type]
    return str(value)


def _print_result(run: DemoRun, evidence_path: Path) -> None:
    interventions = run.interventions.for_episode(run.result.episode_id)
    primitive_names = [str(item.get("action", "")) for item in run.result.primitive_plan]
    print("\n5. RESULT")
    print(f"   primitives        : {' -> '.join(primitive_names)}")
    print(f"   agent actions     : {len(run.executor_calls)}")
    print(f"   human decisions   : {[record.decision for record in interventions]}")
    print(f"   re-observed       : {any(record.reobserved for record in interventions)}")
    print(f"   replanned         : {any(record.replanned for record in interventions)}")
    print(f"   final verified    : {run.result.final_outcome_verified}")
    print(f"   room restored     : {run.state_before == run.state_after}")
    print(f"   evidence          : {evidence_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DEMO_TITLE)
    parser.add_argument("--headed", action="store_true", help="Show the live Chromium window for human takeover")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Run the live environment without terminal input and approve the protected action",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a deterministic in-memory rehearsal without Docker or Chromium",
    )
    parser.add_argument("--room", default="A")
    parser.add_argument("--time", default="14:00")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:3000")
    parser.add_argument("--thing-directory-url", default="http://127.0.0.1:8082/things")
    parser.add_argument("--wot-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--control-url", default="http://127.0.0.1:8081")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run and args.auto_approve:
        print("--dry-run already uses a simulated takeover; do not combine it with --auto-approve", file=sys.stderr)
        return 2
    paths = _prepare_paths(args.output)
    try:
        run = (
            asyncio.run(run_dry_demo(paths, room=args.room, time_slot=args.time))
            if args.dry_run
            else asyncio.run(run_live_demo(args, paths))
        )
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        print(f"Demo could not start: {exc}", file=sys.stderr)
        if not args.dry_run:
            print("Start the environment with: docker compose -f env/docker-compose.yml up --build -d", file=sys.stderr)
        return 1
    evidence_path = write_evidence(run, paths.evidence)
    _print_result(run, evidence_path)
    return 0 if run.result.final_outcome_verified and run.state_before == run.state_after else 1


if __name__ == "__main__":
    raise SystemExit(main())
