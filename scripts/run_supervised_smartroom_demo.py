"""Run the shared supervised smart-room pipeline in one isolated episode.

The default request is deliberately small and visible: book Room C at 15:30,
set the lights, turn on the projector, set the thermostat target, and pause for
human confirmation before the final booking click.

    docker compose -f env/docker-compose.yml up --build -d
    .venv/bin/python scripts/run_supervised_smartroom_demo.py

Use ``--auto-approve`` for an unattended run.  Without it, choose ``t`` at the
pause, click Book Room in the browser, then return to the terminal and press
Enter.  The runtime re-observes and replans before it continues.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_supervised_session_demo import _operator_loop, booking_probes  # noqa: E402
from src.demos.evidence_fusion_panel import (  # noqa: E402
    EvidenceFusionPanel,
    PresentationEpistemicArbiter,
)
from src.effectors.wot_executor import WotExecutor  # noqa: E402
from src.isolation import BrowserWotIsolationProvider  # noqa: E402
from src.planner.agent_planner import AgentPlanner  # noqa: E402
from src.planner.goal_skill_selector import GoalSkillSelector  # noqa: E402
from src.planner.intent_planner import IntentPlanner, available_client  # noqa: E402
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger  # noqa: E402
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec  # noqa: E402
from src.runtime.intervention import InMemoryInterventionBroker, InterventionLedger  # noqa: E402
from src.runtime.live_environment import (  # noqa: E402
    AffordanceSemanticBinding,
    DomStateProbe,
    LiveActivePerceptionProbe,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SmartRoomControlClient,
    SmartRoomLiveEnvironment,
    ThreadedBrowserSession,
    ThreadedDomEffector,
)
from src.skill_library import load_skill_library  # noqa: E402
from src.verification.active_perception import ActivePerceptionResolver  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "supervised_smartroom" / "episode.json"
DEFAULT_UTTERANCE = "book room C at 15:30 and prepare it for my presentation"


def integrated_state_probes() -> list[DomStateProbe]:
    """Read the dashboard projection needed for real DOM/WoT arbitration."""

    return [
        *booking_probes(),
        DomStateProbe(
            "[data-testid='target-temp']",
            "thermostat",
            "targetTemperature",
            value_type="number",
        ),
    ]


class SmartRoomEpisodeAdapter:
    """Small adapter that lets the shared RuntimeEpisodeRunner use the room."""

    def __init__(
        self,
        environment: SmartRoomLiveEnvironment,
        control: SmartRoomControlClient,
        executors: dict[str, Any],
        panel: EvidenceFusionPanel | None = None,
    ) -> None:
        self.environment = environment
        self.control = control
        self._executors = executors
        self.panel = panel

    async def reset(self, spec: RuntimeEpisodeSpec) -> None:
        # This is only used by a non-isolated caller.  The shared isolated path
        # skips it because BrowserWotIsolationProvider owns reset + rollback.
        await self.control.reset()
        await self.environment.session.recreate()
        self.environment.begin_episode(f"reset:{spec.task_id}")

    async def observe(self, request: ObservationRequest):
        observed = await self.environment.observe(request)
        if self.panel is not None:
            self.panel.begin_episode(request.episode_id)
            await self.panel.show_observation(observed, request.reason)
        return observed

    def begin_episode(self, episode_id: str) -> None:
        """Forward the canonical runner's episode boundary to perception."""

        self.environment.begin_episode(episode_id)
        if self.panel is not None:
            self.panel.begin_episode(episode_id)

    def executors(self) -> dict[str, Any]:
        return self._executors


class PacedExecutor:
    """Slow successful actions slightly so a person can follow the live demo."""

    def __init__(self, executor: Any, delay_s: float) -> None:
        self.executor = executor
        self.delay_s = max(0.0, delay_s)

    async def execute(self, *args: Any, **kwargs: Any):
        result = await self.executor.execute(*args, **kwargs)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return result


class ControlledBookingObstructionExecutor:
    """Inject one visible modal immediately before the protected booking click.

    The fault is presentation control, not planner input.  Runtime and
    ``AgentPlanner`` only learn about it from the failed executor result and the
    subsequent fresh browser observation.  This keeps the recovery scene tied
    to an action occurrence instead of a timing race.
    """

    def __init__(
        self,
        executor: Any,
        environment: SmartRoomLiveEnvironment,
        *,
        settle_s: float = 0.15,
    ) -> None:
        self.executor = executor
        self.environment = environment
        self.settle_s = max(0.0, settle_s)
        self.injected = False
        self.target_affordance_id = ""

    async def execute(self, skill_call: Any, observation: Any):
        affordance_id = str(getattr(skill_call, "params", {}).get("affordance_id", ""))
        affordance = self.environment.find_affordance(affordance_id) if affordance_id else None
        stable_key = str(affordance.locator.get("stable_key", "")) if affordance is not None else ""
        if not self.injected and stable_key == "booking.confirm":
            injected = await self.environment.session.evaluate(
                "fault => { window.__injectFault && window.__injectFault(fault); return true; }",
                "overlay_obstruction",
            )
            if not injected:
                raise RuntimeError("the dashboard does not expose its controlled fault hook")
            self.injected = True
            self.target_affordance_id = affordance_id
            if self.settle_s:
                await asyncio.sleep(self.settle_s)
            print("\nCONTROLLED FAULT")
            print("   A room-policy modal now obstructs the approved Book Room action.")
            print("   Runtime was not told the fault label; it must detect and recover from observation.\n")

        result = await self.executor.execute(skill_call, observation)
        if self.injected and affordance_id == self.target_affordance_id:
            result.metadata = {
                **result.metadata,
                "controlled_fault_injected": "booking_obstruction",
                "fault_visible_to_planner": False,
            }
        return result


class PresentationExecutor:
    """Project the real primitive and executor result without owning either."""

    def __init__(self, executor: Any, panel: EvidenceFusionPanel) -> None:
        self.executor = executor
        self.panel = panel

    async def execute(self, skill_call: Any, observation: Any):
        await self.panel.show_action(skill_call)
        result = await self.executor.execute(skill_call, observation)
        await self.panel.show_execution(result)
        return result


def _with_fault(url: str, fault: str) -> str:
    if not fault.strip():
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    query["fault"] = [fault.strip()]
    return urllib.parse.urlunsplit((*parts[:3], urllib.parse.urlencode(query, doseq=True), parts.fragment))


def integrated_bindings() -> list[AffordanceSemanticBinding]:
    """Attach stable goal meaning to discovered DOM and WoT affordances."""

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
            "WOT",
            entity_id="lights",
            state_attribute="brightness",
            state_source_property="brightness",
            label="setBrightness",
            binds_parameter="brightness",
            stable_key="lights.brightness",
            idempotent=True,
        ),
        AffordanceSemanticBinding(
            "WOT",
            entity_id="projector",
            state_attribute="power",
            state_source_property="power",
            label="setPower",
            binds_parameter="power",
            stable_key="projector.power",
            idempotent=True,
        ),
        AffordanceSemanticBinding(
            "WOT",
            entity_id="thermostat",
            state_attribute="targetTemperature",
            state_source_property="targetTemperature",
            label="setTargetTemperature",
            binds_parameter="target_temperature",
            stable_key="thermostat.target_temperature",
            idempotent=True,
        ),
        AffordanceSemanticBinding(
            "DOM",
            entity_id="booking",
            selector="[data-testid='book-room-button']",
            completion_for="prepare_and_confirm_room",
            achieves="booking.confirmed == true",
            stable_key="booking.confirm",
            safety_level="high",
        ),
    ]


def build_runtime_goal(utterance: str, *, use_model: bool) -> tuple[Any, Any, Any]:
    planner = IntentPlanner(client=available_client() if use_model else None)
    plan = planner.plan(utterance)
    if not plan.ok or plan.goal is None:
        raise ValueError(plan.error or "the request did not produce a supported goal")
    if plan.goal.goal_state != "room_session_prepared":
        raise ValueError(f"the request produced {plan.goal.goal_state!r}; this demo requires 'room_session_prepared'")

    library = load_skill_library(REPO_ROOT / "config" / "skills_seed.json")
    selection = GoalSkillSelector(library).select(plan.goal)
    return plan, plan.goal, selection


def build_agent_planner(
    *,
    use_model: bool,
    ledger_path: Path,
    allow_deterministic_recovery: bool = False,
) -> AgentPlanner:
    """Compose the one forward/recovery planner used by the runtime episode."""

    return AgentPlanner(
        client=available_client() if use_model else None,
        ledger_path=ledger_path,
        plan_forward_with_model=use_model,
        allow_deterministic_recovery=allow_deterministic_recovery,
    )


async def run(args: argparse.Namespace) -> int:
    # Keep programmatic callers that construct older Namespaces source-compatible;
    # the CLI parser supplies all presentation and controlled-fault fields.
    inject_booking_obstruction = bool(getattr(args, "inject_booking_obstruction", False))
    fault_settle_delay = float(getattr(args, "fault_settle_delay", 0.15))
    dashboard_fault = str(getattr(args, "fault", ""))
    evidence_panel_enabled = bool(getattr(args, "evidence_panel", True))
    record = bool(getattr(args, "record", False))
    hold = float(getattr(args, "hold", 4.0))
    plan, goal, selection = build_runtime_goal(args.utterance, use_model=args.use_model)
    output = Path(args.evidence).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    transitions_path = output.with_name("transition_ledger.jsonl")
    interventions_path = output.with_name("intervention_ledger.jsonl")
    failures_path = output.with_name("failure_ledger.jsonl")
    planner_path = output.with_name("agent_planner_calls.jsonl")
    for generated in (transitions_path, interventions_path, failures_path, planner_path):
        generated.unlink(missing_ok=True)

    dashboard_url = _with_fault(args.dashboard_url, dashboard_fault)
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=args.thing_directory_url,
        wot_public_base_url=args.wot_base_url,
        control_url=args.control_url,
        settle_after_action_s=args.settle_delay,
        output_dir=output.parent,
    )
    record_dir = output.parent / "raw_video" if record else None
    if record_dir is not None:
        record_dir.mkdir(parents=True, exist_ok=True)
    browser = ThreadedBrowserSession(
        config.dashboard_url,
        headless=not args.headed,
        record_video_dir=str(record_dir) if record_dir is not None else None,
    )
    panel = EvidenceFusionPanel(browser, args.utterance) if evidence_panel_enabled else None
    control = SmartRoomControlClient(config.control_url)
    environment = SmartRoomLiveEnvironment(
        browser,
        config,
        dom_state_probes=integrated_state_probes(),
        semantic_bindings=integrated_bindings(),
        include_wot_state=True,
        allowed_affordance_sources={"DOM", "WOT"},
    )
    active_perception = ActivePerceptionResolver(
        LiveActivePerceptionProbe(environment, clear_dom_faults=True, settle_s=0.65),
        max_attempts=2,
    )
    isolation = BrowserWotIsolationProvider(browser, control)

    dom_executor: Any = RuntimeAffordanceExecutor("dom", environment, ThreadedDomEffector(browser))
    obstruction: ControlledBookingObstructionExecutor | None = None
    if inject_booking_obstruction:
        obstruction = ControlledBookingObstructionExecutor(
            dom_executor,
            environment,
            settle_s=fault_settle_delay,
        )
        dom_executor = obstruction
    wot_executor: Any = RuntimeAffordanceExecutor("wot", environment, WotExecutor())
    if panel is not None:
        dom_executor = PresentationExecutor(dom_executor, panel)
        wot_executor = PresentationExecutor(wot_executor, panel)
    executors = {
        # These stay plain here on purpose. RuntimeEpisodeRunner applies the
        # isolation provider's input guard to every executor at the shared
        # boundary, so other callers receive the same protection automatically.
        "dom": PacedExecutor(dom_executor, args.step_delay),
        "wot": PacedExecutor(wot_executor, args.step_delay),
    }
    adapter = SmartRoomEpisodeAdapter(environment, control, executors, panel)
    transitions = TransitionLedger(transitions_path)
    interventions = InterventionLedger(interventions_path)
    broker = InMemoryInterventionBroker(interventions)
    library = load_skill_library(REPO_ROOT / "config" / "skills_seed.json")
    agent_planner = build_agent_planner(
        use_model=args.use_model,
        ledger_path=planner_path,
        allow_deterministic_recovery=inject_booking_obstruction,
    )
    runner = RuntimeEpisodeRunner(
        skill_library={skill.skill_id: skill for skill in library.all()},
        episode_policy=EpisodePolicy(
            max_steps=12,
            deadline_s=180.0,
            max_retry_attempts=1,
            max_attempts_per_backend=8,
            require_fresh_observation=True,
        ),
        transition_ledger=transitions,
        isolation_provider=isolation,
        intervention_broker=broker,
        intervention_ledger=interventions,
        system2_planner=agent_planner,
        epistemic_arbiter=PresentationEpistemicArbiter(panel) if panel is not None else None,
        active_perception_resolver=active_perception,
    )

    before = await control.state()
    print("\nSUPERVISED SMART-ROOM SESSION")
    print("=" * 34)
    print(f"1. Request          : {args.utterance}")
    print(f"2. Intent source    : {plan.source}")
    print(f"3. Reusable Skill   : {selection.skill_tuple.skill_id}")
    planner_source = (
        "model forward + recovery" if args.use_model and agent_planner.client is not None else "deterministic forward"
    )
    print(f"4. Agent planner    : {planner_source}")
    print("   Planned surfaces : dashboard (DOM) + devices (WoT)")
    print("5. Isolation        : fresh browser + room checkpoint/restore")
    if inject_booking_obstruction:
        print("   Controlled fault : one modal, injected on the first approved booking click")

    runtime_task = asyncio.create_task(
        runner.run_goal_episode(
            adapter,
            RuntimeEpisodeSpec(
                task_id="supervised-smartroom-session",
                goal_id=goal.goal_id,
                goal_state=goal.goal_state,
                parameters=dict(goal.parameters),
                goal_spec=goal,
                data_source="supervised_smartroom_live",
            ),
        )
    )
    operator_task = asyncio.create_task(
        _operator_loop(broker, runtime_task, mode="auto_approve" if args.auto_approve else "interactive")
    )
    try:
        outcome = await runtime_task
        await operator_task
        after = await control.state()
        if panel is not None:
            await panel.flush()
            await panel.show_final(
                verified=outcome.result.final_outcome_verified,
                detail=(
                    f"fresh terminal evidence · state={outcome.result.state.value} · "
                    f"backends={','.join(sorted({record.backend for record in outcome.transition_ledger.records if record.backend})) or 'none'}"
                ),
            )
            panel.write_trace(output.with_name("evidence_panel_trace.json"))
            await asyncio.sleep(max(0.0, hold))
    finally:
        if not operator_task.done():
            await operator_task
        if panel is not None:
            await panel.close()
        await browser.close()

    failures = runner.failure_ledger
    failures.write_jsonl(failures_path)
    records = [asdict(record) for record in outcome.transition_ledger.records]
    intervention_records = [asdict(record) for record in outcome.intervention_ledger.records]
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "mode": "live_auto_approve" if args.auto_approve else "live_interactive",
        "utterance": args.utterance,
        "intent": plan.to_dict(),
        "goal": asdict(goal),
        "selected_skill": selection.skill_tuple.skill_id,
        "agent_planner": {
            "type": type(agent_planner).__name__,
            "model_forward_enabled": agent_planner.plan_forward_with_model,
            "model_configured": agent_planner.client is not None,
            "decisions": [choice.to_dict() for choice in agent_planner.choices],
            "ledger": str(planner_path),
        },
        "result": {
            "state": outcome.result.state.value,
            "reason": outcome.result.reason,
            "verified": outcome.result.final_outcome_verified,
            "episode_id": outcome.result.episode_id,
            "primitive_plan": outcome.result.primitive_plan,
            "transition_ids": outcome.result.transition_ids,
            "goal_skill_selection": outcome.result.goal_skill_selection,
        },
        "surfaces_used": sorted({record["backend"] for record in records if record.get("backend")}),
        "transitions": records,
        "interventions": intervention_records,
        "room_state_before": before,
        "room_state_after": after,
        "room_state_restored": before == after,
        "controlled_fault": {
            "requested": inject_booking_obstruction,
            "type": "booking_obstruction" if inject_booking_obstruction else "",
            "phase": "before_first_booking_confirm_execution" if inject_booking_obstruction else "",
            "applied": bool(obstruction is not None and obstruction.injected),
            "target_affordance_id": obstruction.target_affordance_id if obstruction is not None else "",
            "hidden_from_planner": True if inject_booking_obstruction else None,
        },
        "software_input_gate": "agent executors require the active agent lease",
        "os_input_isolation": False,
        "presentation": {
            "evidence_panel": panel is not None,
            "fault": dashboard_fault,
            "panel_trace": str(output.with_name("evidence_panel_trace.json")) if panel is not None else "",
            "raw_video_dir": str(record_dir or ""),
        },
    }
    output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    print(f"\n6. Runtime result   : {outcome.result.state.value}")
    print(f"7. Backends used    : {', '.join(payload['surfaces_used']) or 'none'}")
    print(f"8. Re-observed      : {any(record.get('reobserved') for record in intervention_records)}")
    print(f"9. State restored   : {payload['room_state_restored']}")
    evidence_label = output.relative_to(REPO_ROOT) if output.is_relative_to(REPO_ROOT) else output
    print(f"10. Evidence        : {evidence_label}\n")
    return 0 if outcome.result.final_outcome_verified and before == after else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--utterance", default=DEFAULT_UTTERANCE)
    parser.add_argument(
        "--use-model",
        action="store_true",
        help="Use a configured LLM for intent plus unified forward/recovery action planning.",
    )
    parser.add_argument("--headless", dest="headed", action="store_false", default=True)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--step-delay", type=float, default=0.8)
    parser.add_argument("--settle-delay", type=float, default=0.25)
    parser.add_argument(
        "--inject-booking-obstruction",
        action="store_true",
        help="Inject one modal immediately before the first approved Book Room click so Runtime must recover.",
    )
    parser.add_argument(
        "--fault-settle-delay",
        type=float,
        default=0.15,
        help="Seconds to let the controlled modal render before the attempted click.",
    )
    parser.add_argument("--hold", type=float, default=4.0, help="Seconds to hold the terminal oracle on screen.")
    parser.add_argument("--record", action="store_true", help="Record the browser context to artifacts.")
    parser.add_argument(
        "--fault",
        default="",
        help="Dashboard fault query, for example overlay_obstruction or stale_temperature.",
    )
    parser.add_argument("--no-evidence-panel", dest="evidence_panel", action="store_false", default=True)
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:3000")
    parser.add_argument("--thing-directory-url", default="http://127.0.0.1:8082/things")
    parser.add_argument("--wot-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--control-url", default="http://127.0.0.1:8081")
    parser.add_argument("--evidence", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"\nDemo could not run: {exc}")
        print("Start the room with: docker compose -f env/docker-compose.yml up --build -d")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
