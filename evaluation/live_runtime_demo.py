"""Real Docker + Playwright tracer-bullet evaluation for runtime control.

Unlike the deterministic adaptation demo, every result here is derived from a
live browser observation, live node-wot property reads, actual executor calls,
and CIM's persisted transition records.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evaluation.metrics_aggregator import aggregate_metrics, dataset_from_runtime_results
from src.adaptation.trace_ledger import TraceLedger
from src.contracts.types import Condition, RollbackSpec, SkillTuple
from src.effectors.wot_executor import WotExecutor
from src.recovery.recovery_cascade import (
    RecoveryAction,
    RecoveryCascade,
    RecoveryDecisionStep,
    RecoveryTrace,
)
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, Executor, RuntimeStepResult
from src.runtime.episode import EpisodePolicy, ObservationRequest, TransitionLedger
from src.runtime.goal_spec import GoalSpec
from src.runtime.live_environment import (
    AffordanceSemanticBinding,
    DomStateProbe,
    FaultClearingExecutor,
    LiveActivePerceptionProbe,
    LiveEnvironmentConfig,
    RuntimeAffordanceExecutor,
    SkillActionBinding,
    SmartRoomControlClient,
    SmartRoomLiveEnvironment,
    ThreadedBrowserSession,
    ThreadedDomEffector,
)
from src.verification.active_perception import ActivePerceptionResolver
from src.verification.conflict_detector import EpistemicArbiter


def run_live_runtime_demo(
    output_dir: str | Path = "artifacts/live_runtime_demo",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
    fusion_strategy: str = "rule_first",
) -> dict[str, str]:
    """Run all live cases with one isolated browser context."""

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    failure_path = target / "failure_ledger.jsonl"
    transition_path.unlink(missing_ok=True)
    failure_path.unlink(missing_ok=True)
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_public_base_url=wot_base_url,
        control_url=control_url,
        output_dir=target,
    )
    return asyncio.run(
        _run_with_browser(
            config,
            transition_path,
            failure_path,
            headless=headless,
            fusion_strategy=fusion_strategy,
        )
    )


def run_live_runtime_ablation(
    output_dir: str | Path = "artifacts/live_runtime_ablation",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
) -> dict[str, str]:
    """Compare runtime modes on the same normal and timeout episodes."""

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transition_path = target / "transition_ledger.jsonl"
    transition_path.unlink(missing_ok=True)
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_public_base_url=wot_base_url,
        control_url=control_url,
        output_dir=target,
    )
    return asyncio.run(_run_ablation_with_browser(config, transition_path, headless=headless))


async def _run_with_browser(
    config: LiveEnvironmentConfig,
    transition_path: Path,
    failure_path: Path,
    *,
    headless: bool,
    fusion_strategy: str,
) -> dict[str, str]:
    session = ThreadedBrowserSession(config.dashboard_url, headless=headless)
    await session.start()
    try:
        return await _run_suite(session, config, transition_path, failure_path, fusion_strategy=fusion_strategy)
    finally:
        await session.close()


async def _run_ablation_with_browser(
    config: LiveEnvironmentConfig,
    transition_path: Path,
    *,
    headless: bool,
) -> dict[str, str]:
    session = ThreadedBrowserSession(config.dashboard_url, headless=headless)
    await session.start()
    try:
        return await _run_ablation(session, config, transition_path)
    finally:
        await session.close()


async def _run_ablation(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    transition_path: Path,
) -> dict[str, str]:
    control = SmartRoomControlClient(config.control_url, timeout_s=config.request_timeout_s)
    ledger = TransitionLedger(transition_path)
    rows: list[dict[str, Any]] = []
    for mode in ("full", "no-recovery", "dom-only", "wot-only"):
        failures = TraceLedger()
        normal, _ = await _normal_goal_case(
            session,
            config,
            control,
            ledger,
            failures,
            mode=mode,
        )
        timeout, _ = await _timeout_recovery_case(
            session,
            config,
            control,
            ledger,
            failures,
            mode=mode,
        )
        results = [normal, timeout]
        report = aggregate_metrics(
            dataset_from_runtime_results(
                results,
                ledger,
                expected_recovery_tiers={timeout.episode_id: 1},
            ),
            data_source="live_ablation",
            episode_ids=[result.episode_id for result in results],
        )
        rows.append(
            {
                "mode": mode,
                "episode_ids": [result.episode_id for result in results],
                "normal_goal_verified": normal.final_outcome_verified,
                "timeout_goal_verified": timeout.final_outcome_verified,
                "timeout_recovery_attempted": timeout.recovery_attempted,
                "timeout_recovery_succeeded": timeout.recovery_succeeded,
                "metrics": report.values,
            }
        )
    await control.reset()
    report_path = config.output_dir / "ablation_report.json"
    _write_json(
        report_path,
        {
            "data_source": "live_ablation",
            "seeded_protocol": {
                "normal": {"goal": "book_room", "room": "C", "time": "15:30"},
                "chaos": {"goal": "set_temperature_live", "target": 22, "fault": "timeout"},
            },
            "modes": rows,
        },
    )
    return {
        "ablation_report": str(report_path),
        "transition_ledger": str(transition_path),
        "screenshots": str(config.output_dir / "screenshots"),
    }


@dataclass(frozen=True)
class _ObservedConflict:
    """One perceptual disagreement, as the episode recorded resolving it.

    Both terms of CRR are observable: the conflict happened because the probe
    was triggered, and it is resolved or it is not. No oracle label is involved.
    """

    conflict_type: str
    resolved: bool


# The surface a skill targets is fixed by how the skill is defined, not by what
# the router picked at run time. Scoring the router against its own choice would
# yield 1.0 by construction, so these labels are written from the case
# definitions above: book_room drives the dashboard form, the temperature skills
# write a WoT property, and the rollback skill dispatches the restore effector.
_EXPECTED_BACKENDS: dict[str, str] = {
    "book_room": "dom",
    "set_temperature_live": "wot",
    "set_temperature_reflex": "wot",
    "restore_temperature_live": "restore",
}


def _observed_conflicts(result: RuntimeStepResult) -> list[_ObservedConflict]:
    return [
        _ObservedConflict(
            conflict_type=str(step.get("action") or "sensory_conflict"),
            resolved=step.get("resolved") is True,
        )
        for step in result.active_perception_trace
    ]


async def _run_suite(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    transition_path: Path,
    failure_path: Path,
    *,
    fusion_strategy: str = "rule_first",
) -> dict[str, str]:
    control = SmartRoomControlClient(config.control_url, timeout_s=config.request_timeout_s)
    transition_ledger = TransitionLedger(transition_path)
    failure_ledger = TraceLedger()
    results: list[RuntimeStepResult] = []
    cases: list[dict[str, Any]] = []

    normal, normal_case = await _normal_goal_case(
        session,
        config,
        control,
        transition_ledger,
        failure_ledger,
        fusion_strategy=fusion_strategy,
    )
    results.append(normal)
    cases.append(normal_case)

    timeout, timeout_case = await _timeout_recovery_case(
        session,
        config,
        control,
        transition_ledger,
        failure_ledger,
        fusion_strategy=fusion_strategy,
    )
    results.append(timeout)
    cases.append(timeout_case)

    rollback, rollback_case = await _postcondition_rollback_case(
        session,
        config,
        control,
        transition_ledger,
        failure_ledger,
        fusion_strategy=fusion_strategy,
    )
    results.append(rollback)
    cases.append(rollback_case)

    conflict, conflict_case = await _conflict_active_perception_case(
        session,
        config,
        control,
        transition_ledger,
        failure_ledger,
        fusion_strategy=fusion_strategy,
    )
    results.append(conflict)
    cases.append(conflict_case)

    reflex_warmup, reflex_repeat, reflex_case = await _system1_reflex_case(
        session,
        config,
        control,
        transition_ledger,
        failure_ledger,
        fusion_strategy=fusion_strategy,
    )
    results.extend([reflex_warmup, reflex_repeat])
    cases.append(reflex_case)
    await control.reset()

    failure_ledger.write_jsonl(failure_path)
    case_path = config.output_dir / "episode_report.json"
    _write_json(
        case_path,
        {
            "data_source": "live",
            "environment": {
                "dashboard_url": config.dashboard_url,
                "thing_directory_url": config.thing_directory_url,
                "wot_public_base_url": config.wot_public_base_url,
                "control_url": config.control_url,
            },
            "all_evidence_checks_passed": all(case["evidence_passed"] for case in cases),
            "cases": cases,
        },
    )

    dataset = dataset_from_runtime_results(
        results,
        transition_ledger,
        expected_recovery_tiers={
            timeout.episode_id: 1,
            rollback.episode_id: 3,
        },
        expected_backends=_EXPECTED_BACKENDS,
        conflicts_by_episode={result.episode_id: _observed_conflicts(result) for result in results},
    )
    metrics = aggregate_metrics(
        dataset,
        data_source="live",
        episode_ids=[result.episode_id for result in results],
    )
    metric_path = config.output_dir / "measured_metrics.json"
    _write_json(metric_path, {"values": metrics.values, "metadata": metrics.metadata})

    recovery_path = config.output_dir / "recovery_report.json"
    _write_json(
        recovery_path,
        {
            "data_source": "live",
            "episodes": [
                {
                    "episode_id": result.episode_id,
                    "state": result.state.value,
                    "failure_type": result.failure_type,
                    "attempts": result.attempts,
                    "recovery_attempted": result.recovery_attempted,
                    "recovery_succeeded": result.recovery_succeeded,
                    "final_outcome_verified": result.final_outcome_verified,
                    "recovery_trace": result.recovery_trace,
                }
                for result in results
            ],
        },
    )
    return {
        "episode_report": str(case_path),
        "transition_ledger": str(transition_path),
        "failure_ledger": str(failure_path),
        "recovery_report": str(recovery_path),
        "measured_metrics": str(metric_path),
        "screenshots": str(config.output_dir / "screenshots"),
    }


async def _normal_goal_case(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    mode: str = "full",
    fusion_strategy: str = "rule_first",
) -> tuple[RuntimeStepResult, dict[str, Any]]:
    await control.reset()
    await session.open(config.dashboard_url)
    task_id = f"live_normal_goal_{mode}"
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        dom_state_probes=_booking_probes(),
        semantic_bindings=_booking_bindings(),
        allowed_affordance_sources=_mode_sources(mode),
    )
    initial = await environment.observe(_initial_request(task_id))
    manager = _goal_manager(
        environment, session, task_id, transitions, failures, mode=mode, fusion_strategy=fusion_strategy
    )
    result = await manager.run_observed_goal(
        initial,
        goal_spec=GoalSpec(
            goal_id="book_room",
            goal_state="booking.confirmed == true",
            parameters={"room": "C", "time": "15:30"},
            source="demo",
            success_evidence=["DOM booking status re-observed as confirmed"],
        ),
    )
    checks = {
        "final_goal_verified": result.final_outcome_verified,
        "planned_multiple_live_actions": len(result.primitive_plan) >= 3,
        "transition_chain_persisted": len(result.transition_ids) >= 3,
    }
    return result, _case_payload("normal_structured_goal", result, checks)


async def _timeout_recovery_case(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    mode: str = "full",
    fusion_strategy: str = "rule_first",
) -> tuple[RuntimeStepResult, dict[str, Any]]:
    await control.reset()
    await session.open(config.dashboard_url)
    fast_timeout_config = replace_config(config, request_timeout_s=0.4)
    task_id = f"live_timeout_retry_{mode}"
    environment = SmartRoomLiveEnvironment(
        session,
        fast_timeout_config,
        semantic_bindings=_temperature_bindings(),
        allowed_affordance_sources=_mode_sources(mode),
    )
    initial = await environment.observe(_initial_request(task_id))
    wot = WotExecutor(environment.tds, timeout_s=0.3)
    wot_executor = RuntimeAffordanceExecutor("wot", environment, wot)
    executors: dict[str, Executor] = {"wot": wot_executor} if mode != "dom-only" else {}
    manager = ContinuousInteractionManager(
        {},
        executors,
        CognitiveMap(task_id=task_id),
        observation_provider=environment,
        episode_policy=_live_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        recovery_cascade=_recovery_for_mode(mode),
        epistemic_arbiter=_arbiter_for_strategy(fusion_strategy),
    )
    await control.inject("thermostat", "timeout", delay_ms=1000)

    async def clear_transient_fault() -> None:
        await asyncio.sleep(0.55)
        await control.clear("thermostat")

    clear_task = asyncio.create_task(clear_transient_fault())
    try:
        result = await manager.run_observed_goal(
            initial,
            goal_spec=GoalSpec(
                goal_id="set_temperature_live",
                goal_state="thermostat.target_temperature == 22",
                parameters={"target": 22},
                source="demo",
                success_evidence=["fresh WoT property read equals 22"],
            ),
        )
    finally:
        await clear_task
        await control.clear("thermostat")
    checks = {
        "transient_failure_triggered_recovery": result.recovery_attempted,
        "same_action_was_retried": result.attempts >= 2,
        "recovery_reached_verified_goal": result.recovery_succeeded and result.final_outcome_verified,
    }
    return result, _case_payload("wot_timeout_retry", result, checks)


async def _postcondition_rollback_case(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    fusion_strategy: str = "rule_first",
) -> tuple[RuntimeStepResult, dict[str, Any]]:
    reset = await control.reset()
    previous = int(reset["state"]["thermostat"]["targetTemperature"])
    await session.open(config.dashboard_url)
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        semantic_bindings=_temperature_bindings(),
    )
    initial = await environment.observe(_initial_request("live_postcondition_rollback"))
    wot = WotExecutor(environment.tds, timeout_s=1.5)
    action_bindings = [
        SkillActionBinding(
            "set_temperature_live",
            "WOT",
            thing_id="thermostat",
            label="setTargetTemperature",
            parameter="target",
        ),
        SkillActionBinding(
            "restore_temperature_live",
            "WOT",
            thing_id="thermostat",
            label="setTargetTemperature",
            parameter="target",
        ),
    ]
    wot_executor = RuntimeAffordanceExecutor("wot", environment, wot, skill_bindings=action_bindings)
    restore_delegate = RuntimeAffordanceExecutor("restore", environment, wot, skill_bindings=action_bindings)
    restore_executor = FaultClearingExecutor("restore", control, "thermostat", restore_delegate)
    skills = {
        "set_temperature_live": SkillTuple(
            skill_id="set_temperature_live",
            description="Set the live thermostat and verify the observed property.",
            parameters_schema={"target": "int"},
            preconditions=[],
            postconditions=[Condition("thermostat.target_temperature == params.target")],
            allowed_backends=["wot"],
            preferred_backends=["wot"],
            rollback=RollbackSpec("restore_temperature_live", {"target": previous}),
            failure_modes={},
            timeout_ms=2000,
            safety_level="low",
            irreversible=False,
            idempotent=False,
        ),
        "restore_temperature_live": SkillTuple(
            skill_id="restore_temperature_live",
            description="Restore the checkpointed thermostat target.",
            parameters_schema={"target": "int"},
            preconditions=[],
            postconditions=[Condition("thermostat.target_temperature == params.target")],
            allowed_backends=["restore"],
            preferred_backends=["restore"],
            rollback=None,
            failure_modes={},
            timeout_ms=2000,
            safety_level="low",
            irreversible=False,
            idempotent=True,
        ),
    }
    manager = ContinuousInteractionManager(
        skills,
        {"wot": wot_executor, "restore": restore_executor},
        CognitiveMap(task_id="live_postcondition_rollback"),
        observation_provider=environment,
        episode_policy=_live_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        recovery_cascade=RecoveryCascade(),
        epistemic_arbiter=_arbiter_for_strategy(fusion_strategy),
    )
    initial.apply_affordances_to(manager.cognitive_map)
    await control.inject("thermostat", "postcondition_mismatch")
    result = await manager.run_skill(
        skill_call=_skill_call("set_temperature_live", {"target": previous + 3}),
        observation=initial.observation,
    )
    records = transitions.for_episode(result.episode_id)
    checks = {
        "false_http_success_detected": any(
            record.execution_success and record.postcondition_passed is False for record in records
        ),
        "rollback_was_executed": any(record.recovery_action == "rollback" for record in records),
        "checkpoint_restore_verified": result.recovery_succeeded
        and any(record.reversible_result is True for record in records),
        "original_goal_not_falsely_claimed": not result.final_outcome_verified,
    }
    return result, _case_payload("postcondition_mismatch_rollback", result, checks)


async def _conflict_active_perception_case(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    fusion_strategy: str = "rule_first",
) -> tuple[RuntimeStepResult, dict[str, Any]]:
    await control.reset()
    await session.open(f"{config.dashboard_url}/?fault=stale_temperature")
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        dom_state_probes=[*_booking_probes(), _temperature_dom_probe()],
        semantic_bindings=_booking_bindings(),
    )
    initial = await environment.observe(_initial_request("live_conflict_resolution"))
    probe = LiveActivePerceptionProbe(environment, clear_dom_faults=True, settle_s=0.2)
    manager = _goal_manager(
        environment,
        session,
        "live_conflict_resolution",
        transitions,
        failures,
        active_perception_resolver=ActivePerceptionResolver(probe, max_attempts=2),
        fusion_strategy=fusion_strategy,
    )
    result = await manager.run_observed_goal(
        initial,
        goal_spec=GoalSpec(
            goal_id="book_room",
            goal_state="booking.confirmed == true",
            parameters={"room": "A", "time": "16:00"},
            source="demo",
        ),
    )
    checks = {
        "active_perception_was_triggered": bool(result.active_perception_trace),
        "conflict_was_resolved": any(step.get("resolved") is True for step in result.active_perception_trace),
        "system1_continued_only_after_resolution": result.final_outcome_verified,
    }
    return result, _case_payload("dom_wot_conflict_active_perception", result, checks)


async def _system1_reflex_case(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    control: SmartRoomControlClient,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    fusion_strategy: str = "rule_first",
) -> tuple[RuntimeStepResult, RuntimeStepResult, dict[str, Any]]:
    await control.reset()
    await session.open(config.dashboard_url)
    environment = SmartRoomLiveEnvironment(
        session,
        config,
        semantic_bindings=[
            AffordanceSemanticBinding(
                "WOT",
                thing_id="thermostat",
                label="setTargetTemperature",
                stable_key="thermostat.set_target_temperature",
                idempotent=True,
                skill_id="set_temperature_reflex",
            )
        ],
        allowed_affordance_sources={"WOT"},
    )
    initial = await environment.observe(_initial_request("live_system1_reflex"))
    wot = WotExecutor(environment.tds, timeout_s=1.5)
    binding = SkillActionBinding(
        "set_temperature_reflex",
        "WOT",
        thing_id="thermostat",
        label="setTargetTemperature",
        parameter="target",
    )
    executor = RuntimeAffordanceExecutor("wot", environment, wot, skill_bindings=[binding])
    skill = SkillTuple(
        skill_id="set_temperature_reflex",
        description="Set and verify temperature for the live System-1 cache path.",
        parameters_schema={"target": "int"},
        preconditions=[],
        postconditions=[Condition("thermostat.target_temperature == params.target")],
        allowed_backends=["wot"],
        preferred_backends=["wot"],
        rollback=None,
        failure_modes={},
        timeout_ms=2000,
        safety_level="low",
        irreversible=False,
        idempotent=True,
    )
    manager = ContinuousInteractionManager(
        {skill.skill_id: skill},
        {"wot": executor},
        CognitiveMap(task_id="live_system1_reflex"),
        observation_provider=environment,
        episode_policy=_live_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        epistemic_arbiter=_arbiter_for_strategy(fusion_strategy),
    )
    initial.apply_affordances_to(manager.cognitive_map)
    warmup = await manager.run_skill(
        _skill_call(skill.skill_id, {"target": 21}),
        initial.observation,
    )
    repeat_observation = await environment.observe(_initial_request("live_system1_reflex"))
    repeat_observation.apply_affordances_to(manager.cognitive_map)
    repeat = await manager.run_skill(
        _skill_call(skill.skill_id, {"target": 21}),
        repeat_observation.observation,
    )
    checks = {
        "warmup_verified": warmup.final_outcome_verified and not warmup.system1_cache_hit,
        "repeat_cache_hit": repeat.system1_cache_hit,
        "repeat_used_system1_fast_path": repeat.system1_fast_path,
        "repeat_still_verified": repeat.final_outcome_verified,
        "routing_latency_recorded": repeat.system1_routing_latency_ms >= 0,
        "latency_report_links_both_episodes": bool(warmup.episode_id and repeat.episode_id),
    }
    payload = _case_payload("system1_reflex_repeat", repeat, checks)
    payload["warmup_episode_id"] = warmup.episode_id
    payload["system1_latency_report"] = _system1_latency_report(warmup, repeat, transitions)
    return warmup, repeat, payload


def _goal_manager(
    environment: SmartRoomLiveEnvironment,
    session: ThreadedBrowserSession,
    task_id: str,
    transitions: TransitionLedger,
    failures: TraceLedger,
    *,
    active_perception_resolver: ActivePerceptionResolver | None = None,
    mode: str = "full",
    fusion_strategy: str = "rule_first",
) -> ContinuousInteractionManager:
    dom = RuntimeAffordanceExecutor("dom", environment, ThreadedDomEffector(session))
    wot = RuntimeAffordanceExecutor("wot", environment, WotExecutor(environment.tds))
    executors: dict[str, Executor] = {"dom": dom, "wot": wot}
    if mode == "dom-only":
        executors = {"dom": dom}
    elif mode == "wot-only":
        executors = {"wot": wot}
    return ContinuousInteractionManager(
        {},
        executors,
        CognitiveMap(task_id=task_id),
        observation_provider=environment,
        episode_policy=_live_policy(),
        transition_ledger=transitions,
        failure_ledger=failures,
        active_perception_resolver=active_perception_resolver,
        recovery_cascade=_recovery_for_mode(mode),
        epistemic_arbiter=_arbiter_for_strategy(fusion_strategy),
    )


def _arbiter_for_strategy(fusion_strategy: str) -> EpistemicArbiter:
    return EpistemicArbiter(fusion_strategy=fusion_strategy)  # type: ignore[arg-type]


class _NoRecoveryCascade(RecoveryCascade):
    def select_with_trace(self, *args: Any, **kwargs: Any) -> tuple[RecoveryAction, RecoveryTrace]:
        _ = (args, kwargs)
        reason = "recovery disabled by ablation mode"
        action = RecoveryAction("escalate_human", recovery_tier=4, reason=reason)
        return action, RecoveryTrace(
            failure_type="ablation_no_recovery",
            boundary="recoverable_execution_failure",
            steps=[RecoveryDecisionStep(4, "no_recovery_ablation", True, True, reason)],
            selected_action="escalate_human",
            selected_tier=4,
            selected_reason=reason,
        )


def _recovery_for_mode(mode: str) -> RecoveryCascade:
    return _NoRecoveryCascade() if mode == "no-recovery" else RecoveryCascade()


def _mode_sources(mode: str) -> set[str] | None:
    if mode == "dom-only":
        return {"DOM"}
    if mode == "wot-only":
        return {"WOT"}
    return None


def _booking_bindings() -> list[AffordanceSemanticBinding]:
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
            selector="[data-testid='book-room-button']",
            completion_for="book_room",
            achieves="booking.confirmed == true",
            stable_key="booking.confirm",
        ),
    ]


def _temperature_bindings() -> list[AffordanceSemanticBinding]:
    return [
        AffordanceSemanticBinding(
            "WOT",
            entity_id="thermostat",
            state_attribute="target_temperature",
            thing_id="thermostat",
            label="setTargetTemperature",
            binds_parameter="target",
            achieves="thermostat.target_temperature == 22",
            stable_key="thermostat.set_target_temperature",
            idempotent=True,
            skill_id="set_temperature_live",
        )
    ]


def _booking_probes() -> list[DomStateProbe]:
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


def _temperature_dom_probe() -> DomStateProbe:
    return DomStateProbe(
        "[data-testid='target-temp']",
        "thermostat",
        "target_temperature",
        value_type="number",
    )


def _initial_request(task_id: str) -> ObservationRequest:
    return ObservationRequest(task_id=task_id, episode_id="preflight", reason="initial_scan", step=0)


def _live_policy() -> EpisodePolicy:
    return EpisodePolicy(
        max_steps=12,
        deadline_s=45.0,
        max_retry_attempts=2,
        max_attempts_per_backend=5,
        require_fresh_observation=True,
    )


def _skill_call(skill_id: str, params: dict[str, Any]) -> Any:
    from src.contracts.types import SkillCall

    return SkillCall(skill_id, params)


def replace_config(config: LiveEnvironmentConfig, **changes: Any) -> LiveEnvironmentConfig:
    from dataclasses import replace

    return replace(config, **changes)


def _case_payload(name: str, result: RuntimeStepResult, checks: dict[str, bool]) -> dict[str, Any]:
    return {
        "case": name,
        "evidence_passed": all(checks.values()),
        "evidence_checks": checks,
        "result": _result_payload(result),
    }


def _system1_latency_report(
    warmup: RuntimeStepResult,
    repeat: RuntimeStepResult,
    transitions: TransitionLedger,
) -> dict[str, Any]:
    """Summarize warm-up vs repeated System-1 routing latency from live evidence."""

    warmup_records = transitions.for_episode(warmup.episode_id)
    repeat_records = transitions.for_episode(repeat.episode_id)
    warmup_transition_latency = _episode_transition_latency_ms(warmup, warmup_records)
    repeat_transition_latency = _episode_transition_latency_ms(repeat, repeat_records)
    episodes = [warmup, repeat]
    total_transition_latency = warmup_transition_latency + repeat_transition_latency
    cache_hits = sum(1 for result in episodes if result.system1_cache_hit)
    return {
        "episode_ids": [warmup.episode_id, repeat.episode_id],
        "cache_hit_rate": round(cache_hits / len(episodes), 3),
        "total_transition_latency_ms": round(total_transition_latency, 3),
        "amortized_transition_latency_ms": round(total_transition_latency / len(episodes), 3),
        "warmup": _system1_episode_latency_payload(warmup, warmup_transition_latency, warmup_records),
        "repeat": _system1_episode_latency_payload(repeat, repeat_transition_latency, repeat_records),
    }


def _episode_transition_latency_ms(result: RuntimeStepResult, records: list[Any]) -> float:
    if records:
        return round(sum(float(record.latency_ms) for record in records), 3)
    if result.execution_result is not None:
        return round(float(result.execution_result.latency_ms), 3)
    return 0.0


def _system1_episode_latency_payload(
    result: RuntimeStepResult,
    transition_latency_ms: float,
    records: list[Any],
) -> dict[str, Any]:
    return {
        "episode_id": result.episode_id,
        "cache_hit": result.system1_cache_hit,
        "fast_path": result.system1_fast_path,
        "routing_latency_ms": round(float(result.system1_routing_latency_ms), 3),
        "transition_latency_ms": transition_latency_ms,
        "total_episode_latency_ms": transition_latency_ms,
        "transition_count": len(records),
        "verified": result.final_outcome_verified,
    }


def _result_payload(result: RuntimeStepResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["state"] = result.state.value
    execution = payload.get("execution_result")
    if isinstance(execution, dict):
        execution.pop("raw_observation_delta", None)
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
