"""Repeated smart-room fusion/recovery campaign protocol.

The live calibration tracer bullet proves the seven scenario labels once. This
module provides the next-stage repeated-trial scaffold: deterministic seeds,
unique episode IDs, explicit reset evidence, independent oracle labels, and
condition-level summary metrics.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

from evaluation.live_fusion_calibration import SCENARIOS, LiveFusionScenario
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.episode import ObservationRequest
from src.runtime.live_environment import (
    DomStateProbe,
    LiveEnvironmentConfig,
    SmartRoomControlClient,
    SmartRoomLiveEnvironment,
    ThreadedBrowserSession,
)
from src.verification.conflict_detector import EpistemicArbiter

ORACLE_SOURCE = "fault-injection-label"


@dataclass(frozen=True)
class RepeatedFusionTrial:
    scenario: str
    repetition: int
    seed: int
    episode_id: str
    expected_blocking: bool
    detected_blocking: bool
    conflict_score: float
    detection_latency_ms: float
    source_pair: str
    reset_evidence_id: str
    oracle_source: str
    conflict_type: str = ""


def build_repeated_fusion_plan(
    *,
    repetitions: int = 30,
    seed_start: int = 1000,
    scenarios: Sequence[LiveFusionScenario] = SCENARIOS,
) -> list[RepeatedFusionTrial]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    plan: list[RepeatedFusionTrial] = []
    seed = seed_start
    for scenario in scenarios:
        for repetition in range(repetitions):
            plan.append(
                RepeatedFusionTrial(
                    scenario=scenario.name,
                    repetition=repetition,
                    seed=seed,
                    episode_id=_episode_id(scenario.name, repetition, seed),
                    expected_blocking=scenario.expected_blocking,
                    detected_blocking=False,
                    conflict_score=0.0,
                    detection_latency_ms=0.0,
                    source_pair="DOM+WOT",
                    reset_evidence_id="",
                    oracle_source=ORACLE_SOURCE,
                )
            )
            seed += 1
    return plan


def summarize_repeated_fusion_campaign(
    trials: Iterable[RepeatedFusionTrial],
    *,
    required_scenarios: Sequence[str] | None = None,
    minimum_repetitions: int = 30,
) -> dict[str, object]:
    materialized = list(trials)
    scenario_names = list(required_scenarios or [scenario.name for scenario in SCENARIOS])
    condition_counts = {
        scenario: sum(1 for trial in materialized if trial.scenario == scenario) for scenario in scenario_names
    }
    episode_ids = [trial.episode_id for trial in materialized]
    seeds = [trial.seed for trial in materialized]
    tp = sum(1 for trial in materialized if trial.expected_blocking and trial.detected_blocking)
    fp = sum(1 for trial in materialized if not trial.expected_blocking and trial.detected_blocking)
    tn = sum(1 for trial in materialized if not trial.expected_blocking and not trial.detected_blocking)
    fn = sum(1 for trial in materialized if trial.expected_blocking and not trial.detected_blocking)
    recall = _divide(tp, tp + fn)
    false_halt_rate = _divide(fp, fp + tn)
    specificity = _divide(tn, tn + fp)
    return {
        "data_source": "live_repeated_fusion_campaign",
        "condition_counts": condition_counts,
        "protocol": {
            "trial_count": len(materialized),
            "required_conditions": scenario_names,
            "minimum_repetitions": minimum_repetitions,
            "minimum_repetitions_met": all(count >= minimum_repetitions for count in condition_counts.values()),
            "unique_episode_ids": len(episode_ids) == len(set(episode_ids)),
            "unique_seeds": len(seeds) == len(set(seeds)),
            "reset_evidence_complete": all(bool(trial.reset_evidence_id) for trial in materialized),
            "independent_oracle_complete": all(trial.oracle_source == ORACLE_SOURCE for trial in materialized),
        },
        "metrics": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "precision": _divide(tp, tp + fp),
            "recall": recall,
            "false_halt_rate": false_halt_rate,
            "miss_rate": _divide(fn, tp + fn),
            "balanced_accuracy": (recall + specificity) / 2,
            "mean_detection_latency_ms": _mean([trial.detection_latency_ms for trial in materialized]),
        },
        "trials": [asdict(trial) for trial in materialized],
    }


def run_live_repeated_fusion_campaign(
    output_dir: str | Path = "artifacts/live_fusion_campaign",
    *,
    repetitions: int = 30,
    seed_start: int = 1000,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
    dry_run: bool = False,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    plan = build_repeated_fusion_plan(repetitions=repetitions, seed_start=seed_start)
    plan_path = target / "fusion_campaign_plan.json"
    plan_path.write_text(json.dumps([asdict(trial) for trial in plan], indent=2, sort_keys=True) + "\n")
    if dry_run:
        summary_path = target / "fusion_campaign_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "data_source": "live_repeated_fusion_campaign_plan",
                    "dry_run": True,
                    "planned_trial_count": len(plan),
                    "condition_counts": {
                        scenario.name: sum(1 for trial in plan if trial.scenario == scenario.name)
                        for scenario in SCENARIOS
                    },
                    "plan": str(plan_path),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {"fusion_campaign_plan": str(plan_path), "fusion_campaign_summary": str(summary_path)}
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_public_base_url=wot_base_url,
        control_url=control_url,
        output_dir=target,
    )
    return asyncio.run(
        _run_live_campaign(config, plan, summary_path=target / "fusion_campaign_summary.json", headless=headless)
    )


async def _run_live_campaign(
    config: LiveEnvironmentConfig,
    plan: Sequence[RepeatedFusionTrial],
    *,
    summary_path: Path,
    headless: bool,
) -> dict[str, str]:
    session = ThreadedBrowserSession(config.dashboard_url, headless=headless)
    await session.start()
    try:
        trials = await _execute_live_trials(session, config, plan)
    finally:
        await session.close()
    summary = summarize_repeated_fusion_campaign(trials)
    summary["replay_config"] = {
        "dashboard_url": config.dashboard_url,
        "thing_directory_url": config.thing_directory_url,
        "wot_public_base_url": config.wot_public_base_url,
        "control_url": config.control_url,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "fusion_campaign_summary": str(summary_path),
        "screenshots": str(config.output_dir / "screenshots"),
    }


async def _execute_live_trials(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    plan: Sequence[RepeatedFusionTrial],
) -> list[RepeatedFusionTrial]:
    control = SmartRoomControlClient(config.control_url, timeout_s=config.request_timeout_s)
    scenarios = {scenario.name: scenario for scenario in SCENARIOS}
    completed: list[RepeatedFusionTrial] = []
    for planned in plan:
        scenario = scenarios[planned.scenario]
        reset = await control.reset()
        reset_evidence_id = f"{planned.episode_id}:reset:{reset.get('state', {}).get('version', planned.repetition)}"
        if scenario.wot_fault:
            await control.inject("thermostat", scenario.wot_fault, delay_ms=scenario.fault_delay_ms)
        url = config.dashboard_url
        if scenario.dom_fault:
            url = f"{url}/?fault={scenario.dom_fault}&seed={planned.seed}&rep={planned.repetition}"
        await session.open(url)
        scenario_config = replace(
            config,
            request_timeout_s=0.25 if scenario.wot_fault in {"timeout", "offline"} else config.request_timeout_s,
        )
        environment = SmartRoomLiveEnvironment(
            session,
            scenario_config,
            dom_state_probes=[
                DomStateProbe(
                    "[data-testid='target-temp']",
                    "thermostat",
                    "target_temperature",
                    value_type="number",
                )
            ],
            allowed_affordance_sources=set(),
        )
        live = await environment.observe(
            ObservationRequest(
                task_id=f"fusion_campaign_{scenario.name}",
                episode_id=planned.episode_id,
                reason=f"campaign_trial_seed_{planned.seed}",
                step=0,
            )
        )
        cognitive_map = CognitiveMap(task_id=f"fusion_campaign_{scenario.name}")
        live.apply_to(cognitive_map)
        arbiter = EpistemicArbiter(
            numeric_tolerances={"target_temperature": 2.0},
            halt_threshold=0.0001,
            required_sources_by_attribute={"target_temperature": {"dom", "wot"}},
            missing_source_mass=1.0,
        )
        started = time.perf_counter()
        decision = arbiter.fuse(cognitive_map)
        latency_ms = (time.perf_counter() - started) * 1000
        strongest = max(decision.conflicts, key=lambda conflict: conflict.conflict_mass, default=None)
        completed.append(
            RepeatedFusionTrial(
                scenario=planned.scenario,
                repetition=planned.repetition,
                seed=planned.seed,
                episode_id=planned.episode_id,
                expected_blocking=planned.expected_blocking,
                detected_blocking=not decision.allow_system1,
                conflict_score=strongest.conflict_mass if strongest is not None else 0.0,
                detection_latency_ms=latency_ms,
                source_pair="DOM+WOT",
                reset_evidence_id=reset_evidence_id,
                oracle_source=ORACLE_SOURCE,
                conflict_type=strongest.conflict_type if strongest is not None else "",
            )
        )
    await control.reset()
    return completed


def _episode_id(scenario: str, repetition: int, seed: int) -> str:
    return f"fusion_{scenario}_rep_{repetition:03d}_seed_{seed}"


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
