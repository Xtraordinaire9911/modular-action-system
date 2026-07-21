"""Labeled live campaign for calibrating the rule-first fusion threshold."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

from evaluation.fusion_calibration import FusionTrial, calibrate_fusion_thresholds
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


@dataclass(frozen=True)
class LiveFusionScenario:
    name: str
    expected_blocking: bool
    dom_fault: str = ""
    wot_fault: str = ""
    fault_delay_ms: int = 0


SCENARIOS = (
    LiveFusionScenario("clean", False),
    LiveFusionScenario("layout_shift", False, dom_fault="layout_shift"),
    LiveFusionScenario("selector_mutation", False, dom_fault="selector_mutation"),
    LiveFusionScenario("stale_temperature", True, dom_fault="stale_temperature"),
    LiveFusionScenario("wot_timeout", True, wot_fault="timeout", fault_delay_ms=1000),
    LiveFusionScenario("wot_offline", True, wot_fault="offline"),
    # This fault must be caught by postcondition verification, not mislabeled
    # as an observation-source conflict before an action is attempted.
    LiveFusionScenario("postcondition_mismatch_pre_action", False, wot_fault="postcondition_mismatch"),
)


def run_live_fusion_calibration(
    output_dir: str | Path = "artifacts/live_fusion_calibration",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_public_base_url=wot_base_url,
        control_url=control_url,
        output_dir=target,
    )
    return asyncio.run(_run_with_browser(config, headless=headless))


async def _run_with_browser(config: LiveEnvironmentConfig, *, headless: bool) -> dict[str, str]:
    session = ThreadedBrowserSession(config.dashboard_url, headless=headless)
    await session.start()
    try:
        report = await _run_campaign(session, config)
    finally:
        await session.close()
    path = config.output_dir / "fusion_calibration_report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "fusion_calibration_report": str(path),
        "screenshots": str(config.output_dir / "screenshots"),
    }


async def _run_campaign(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
) -> dict[str, object]:
    control = SmartRoomControlClient(config.control_url, timeout_s=config.request_timeout_s)
    trials: list[FusionTrial] = []
    for scenario in SCENARIOS:
        await control.reset()
        if scenario.wot_fault:
            await control.inject(
                "thermostat",
                scenario.wot_fault,
                delay_ms=scenario.fault_delay_ms,
            )
        url = config.dashboard_url
        if scenario.dom_fault:
            url = f"{url}/?fault={scenario.dom_fault}"
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
                task_id=f"fusion_{scenario.name}",
                episode_id="calibration",
                reason="labeled_trial",
                step=0,
            )
        )
        cognitive_map = CognitiveMap(task_id=f"fusion_{scenario.name}")
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
        trials.append(
            FusionTrial(
                scenario=scenario.name,
                expected_blocking=scenario.expected_blocking,
                conflict_score=strongest.conflict_mass if strongest is not None else 0.0,
                detection_latency_ms=latency_ms,
                source_pair="DOM+WOT",
                conflict_type=strongest.conflict_type if strongest is not None else "",
            )
        )
    await control.reset()
    calibrated = calibrate_fusion_thresholds(trials)
    return {
        "data_source": "live_calibration",
        "oracle": "fault-injection scenario labels",
        "fusion_model": "rule_first_required_source_aware",
        **calibrated,
    }
