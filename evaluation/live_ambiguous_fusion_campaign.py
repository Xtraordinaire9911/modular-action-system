"""Live ambiguous fusion campaign scaffold.

This is the bridge between the synthetic noisy stress set and future richer
smart-room faults. It maps ambiguous profiles onto the live fault controls that
exist today, records the mapping explicitly, and keeps the production gate
unchanged.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlencode

from evaluation.noisy_fusion_stress import build_noisy_fusion_stress_report
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


ORACLE_SOURCE = "ambiguous-fault-profile-label"


@dataclass(frozen=True)
class LiveAmbiguousProfile:
    name: str
    expected_blocking: bool
    dom_fault: str = ""
    wot_fault: str = ""
    fault_delay_ms: int = 0
    stale_offset: float | None = None
    read_delay_ms: int | None = None
    drop_probability: float | None = None
    source_reliability: dict[str, float] | None = None
    request_timeout_s: float | None = None
    notes: str = ""

    def fault_mapping(self) -> dict[str, Any]:
        return {
            "dom_fault": self.dom_fault,
            "wot_fault": self.wot_fault,
            "fault_delay_ms": self.fault_delay_ms,
            "stale_offset": self.stale_offset,
            "read_delay_ms": self.read_delay_ms,
            "drop_probability": self.drop_probability,
            "source_reliability": self.source_reliability or {},
            "request_timeout_s": self.request_timeout_s,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class LiveAmbiguousTrial:
    profile: str
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
    live_fault_mapping: dict[str, Any]
    conflict_type: str = ""

    def with_result(
        self,
        *,
        conflict_score: float,
        detected_blocking: bool,
        detection_latency_ms: float,
        conflict_type: str = "",
        reset_evidence_id: str | None = None,
    ) -> "LiveAmbiguousTrial":
        return LiveAmbiguousTrial(
            profile=self.profile,
            repetition=self.repetition,
            seed=self.seed,
            episode_id=self.episode_id,
            expected_blocking=self.expected_blocking,
            detected_blocking=detected_blocking,
            conflict_score=conflict_score,
            detection_latency_ms=detection_latency_ms,
            source_pair=self.source_pair,
            reset_evidence_id=reset_evidence_id if reset_evidence_id is not None else self.reset_evidence_id,
            oracle_source=self.oracle_source,
            live_fault_mapping=self.live_fault_mapping,
            conflict_type=conflict_type,
        )


LIVE_AMBIGUOUS_PROFILES = (
    LiveAmbiguousProfile(
        "weak_stale_signal",
        True,
        dom_fault="stale_temperature",
        stale_offset=-1.5,
        source_reliability={"dom": 0.55, "wot": 0.85},
        notes="Weak dashboard stale offset with source reliability metadata.",
    ),
    LiveAmbiguousProfile(
        "delayed_wot_recovery",
        True,
        wot_fault="timeout",
        fault_delay_ms=450,
        read_delay_ms=450,
        source_reliability={"dom": 0.6, "wot": 0.9},
        request_timeout_s=0.25,
        notes="Read delay creates an ambiguous delayed-recovery window.",
    ),
    LiveAmbiguousProfile(
        "low_reliability_dom",
        False,
        dom_fault="layout_shift",
        source_reliability={"dom": 0.3, "wot": 0.95},
        notes="Non-semantic DOM layout noise with low DOM reliability metadata.",
    ),
    LiveAmbiguousProfile(
        "partial_missing_wot",
        True,
        wot_fault="offline",
        drop_probability=0.7,
        source_reliability={"dom": 0.65, "wot": 0.35},
        request_timeout_s=0.25,
        notes="Partial missing WoT reads through drop probability.",
    ),
)


def build_live_ambiguous_fusion_plan(
    *,
    repetitions: int = 30,
    seed_start: int = 4000,
    profiles: Sequence[LiveAmbiguousProfile] = LIVE_AMBIGUOUS_PROFILES,
) -> list[LiveAmbiguousTrial]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    plan: list[LiveAmbiguousTrial] = []
    seed = seed_start
    for profile in profiles:
        for repetition in range(repetitions):
            plan.append(
                LiveAmbiguousTrial(
                    profile=profile.name,
                    repetition=repetition,
                    seed=seed,
                    episode_id=f"ambiguous_{profile.name}_rep_{repetition:03d}_seed_{seed}",
                    expected_blocking=profile.expected_blocking,
                    detected_blocking=False,
                    conflict_score=0.0,
                    detection_latency_ms=0.0,
                    source_pair="DOM+WOT",
                    reset_evidence_id="",
                    oracle_source=ORACLE_SOURCE,
                    live_fault_mapping=profile.fault_mapping(),
                )
            )
            seed += 1
    return plan


def summarize_live_ambiguous_fusion_trials(
    trials: Iterable[LiveAmbiguousTrial],
    *,
    rule_threshold: float = 1.0,
    posterior_threshold: float = 0.5,
) -> dict[str, Any]:
    materialized = list(trials)
    stress_like_rows = [
        {
            "scenario": trial.profile,
            "repetition": trial.repetition,
            "seed": trial.seed,
            "episode_id": trial.episode_id,
            "expected_blocking": trial.expected_blocking,
            "conflict_score": trial.conflict_score,
            "detection_latency_ms": trial.detection_latency_ms,
            "source_pair": trial.source_pair,
            "source_reliability": _source_reliability_from_mapping(trial.live_fault_mapping),
            "staleness_ms": _staleness_from_mapping(trial.live_fault_mapping),
            "missing_source_probability": _missing_probability_from_mapping(trial.live_fault_mapping),
            "oracle_source": trial.oracle_source,
        }
        for trial in materialized
    ]
    comparator = build_noisy_fusion_stress_report(
        stress_like_rows,
        rule_threshold=rule_threshold,
        posterior_threshold=posterior_threshold,
    )
    return {
        "data_source": "live_ambiguous_fusion_campaign",
        "protocol": {
            "trial_count": len(materialized),
            "profile_counts": _profile_counts(materialized),
            "oracle_source": ORACLE_SOURCE,
            "production_gate_changed": False,
            "fine_grained_fault_api": True,
            "unique_episode_ids": len({trial.episode_id for trial in materialized}) == len(materialized),
            "unique_seeds": len({trial.seed for trial in materialized}) == len(materialized),
            "reset_evidence_complete": all(bool(trial.reset_evidence_id) for trial in materialized),
        },
        "rule_first": comparator["rule_first"],
        "bayesian": comparator["bayesian"],
        "comparison": {
            **comparator["comparison"],
            "production_gate_changed": False,
        },
        "trials": [asdict(trial) for trial in materialized],
    }


def run_live_ambiguous_fusion_campaign(
    output_dir: str | Path = "artifacts/live_ambiguous_fusion_campaign",
    *,
    repetitions: int = 30,
    seed_start: int = 4000,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
    dry_run: bool = False,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    plan = build_live_ambiguous_fusion_plan(repetitions=repetitions, seed_start=seed_start)
    plan_path = target / "live_ambiguous_fusion_plan.json"
    plan_path.write_text(json.dumps([asdict(trial) for trial in plan], indent=2, sort_keys=True) + "\n")
    summary_path = target / "live_ambiguous_fusion_summary.json"
    if dry_run:
        summary_path.write_text(
            json.dumps(
                {
                    "data_source": "live_ambiguous_fusion_campaign_plan",
                    "dry_run": True,
                    "planned_trial_count": len(plan),
                    "profile_counts": _profile_counts(plan),
                    "fine_grained_fault_api": True,
                    "plan": str(plan_path),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {"live_ambiguous_fusion_plan": str(plan_path), "live_ambiguous_fusion_summary": str(summary_path)}
    config = LiveEnvironmentConfig(
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_public_base_url=wot_base_url,
        control_url=control_url,
        output_dir=target,
    )
    return asyncio.run(_run_with_browser(config, plan, summary_path=summary_path, headless=headless))


async def _run_with_browser(
    config: LiveEnvironmentConfig,
    plan: Sequence[LiveAmbiguousTrial],
    *,
    summary_path: Path,
    headless: bool,
) -> dict[str, str]:
    session = ThreadedBrowserSession(config.dashboard_url, headless=headless)
    await session.start()
    try:
        trials = await _execute_live_ambiguous_trials(session, config, plan)
    finally:
        await session.close()
    summary = summarize_live_ambiguous_fusion_trials(trials)
    summary["replay_config"] = {
        "dashboard_url": config.dashboard_url,
        "thing_directory_url": config.thing_directory_url,
        "wot_public_base_url": config.wot_public_base_url,
        "control_url": config.control_url,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "live_ambiguous_fusion_summary": str(summary_path),
        "screenshots": str(config.output_dir / "screenshots"),
    }


async def _execute_live_ambiguous_trials(
    session: ThreadedBrowserSession,
    config: LiveEnvironmentConfig,
    plan: Sequence[LiveAmbiguousTrial],
) -> list[LiveAmbiguousTrial]:
    profiles = {profile.name: profile for profile in LIVE_AMBIGUOUS_PROFILES}
    control = SmartRoomControlClient(config.control_url, timeout_s=config.request_timeout_s)
    completed: list[LiveAmbiguousTrial] = []
    for planned in plan:
        profile = profiles[planned.profile]
        reset = await control.reset()
        reset_evidence_id = f"{planned.episode_id}:reset:{reset.get('state', {}).get('version', planned.repetition)}"
        if profile.wot_fault:
            await control.inject(
                "thermostat",
                profile.wot_fault,
                delay_ms=profile.fault_delay_ms,
                read_delay_ms=profile.read_delay_ms,
                drop_probability=profile.drop_probability,
                source_reliability=profile.source_reliability,
            )
        url = config.dashboard_url
        if profile.dom_fault:
            params: dict[str, Any] = {
                "fault": profile.dom_fault,
                "seed": planned.seed,
                "rep": planned.repetition,
            }
            if profile.stale_offset is not None:
                params["stale_offset"] = profile.stale_offset
            if profile.source_reliability:
                params["source_reliability"] = json.dumps(profile.source_reliability, sort_keys=True)
            url = f"{url}/?{urlencode(params)}"
        await session.open(url)
        scenario_config = replace(
            config,
            request_timeout_s=profile.request_timeout_s or config.request_timeout_s,
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
                task_id=f"live_ambiguous_{profile.name}",
                episode_id=planned.episode_id,
                reason=f"ambiguous_profile_seed_{planned.seed}",
                step=0,
            )
        )
        cognitive_map = CognitiveMap(task_id=f"live_ambiguous_{profile.name}")
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
            planned.with_result(
                conflict_score=strongest.conflict_mass if strongest is not None else 0.0,
                detected_blocking=not decision.allow_system1,
                detection_latency_ms=latency_ms,
                conflict_type=strongest.conflict_type if strongest is not None else "",
                reset_evidence_id=reset_evidence_id,
            )
        )
    await control.reset()
    return completed


def _profile_counts(trials: Sequence[LiveAmbiguousTrial]) -> dict[str, int]:
    profiles = sorted({trial.profile for trial in trials})
    return {profile: sum(1 for trial in trials if trial.profile == profile) for profile in profiles}


def _source_reliability_from_mapping(mapping: dict[str, Any]) -> dict[str, float]:
    if mapping.get("source_reliability"):
        return {str(key): float(value) for key, value in dict(mapping["source_reliability"]).items()}
    if mapping.get("dom_fault") == "layout_shift":
        return {"dom": 0.3, "wot": 0.95}
    if mapping.get("wot_fault") == "offline":
        return {"dom": 0.65, "wot": 0.35}
    return {"dom": 0.55, "wot": 0.85}


def _staleness_from_mapping(mapping: dict[str, Any]) -> float:
    if mapping.get("stale_offset") is not None:
        return min(2000.0, abs(float(mapping["stale_offset"])) * 800.0)
    if mapping.get("read_delay_ms") is not None:
        return float(mapping["read_delay_ms"]) * 2.0
    if mapping.get("dom_fault") == "stale_temperature":
        return 1200.0
    if mapping.get("wot_fault") == "timeout":
        return 900.0
    return 100.0


def _missing_probability_from_mapping(mapping: dict[str, Any]) -> float:
    if mapping.get("drop_probability") is not None:
        return float(mapping["drop_probability"])
    if mapping.get("wot_fault") == "offline":
        return 0.7
    if mapping.get("wot_fault") == "timeout":
        return 0.15
    return 0.0
