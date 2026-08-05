"""Top-level runtime entry point.

The Week-6 demo has concrete DOM/WoT/Visual perception and executor modules;
this file keeps a tiny deterministic orchestration smoke test for CI while also
offering a white-box runtime demo entry point that does not require browser or
Docker services.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from src.contracts.types import Condition, ExecutionResult, Observation, RollbackSpec, SkillCall, SkillTuple
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec, StaticRuntimeEnvironmentAdapter


class NoOpExecutor:
    """Executor used only for CI smoke runs that avoid browser/Docker services."""

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="noop",
            success=True,
            latency_ms=0.0,
            confidence=1.0,
            raw_observation_delta={"pipeline": {"smoke_completed": True}},
        )


def build_smoke_skill_library() -> dict[str, SkillTuple]:
    return {
        "pipeline_smoke": SkillTuple(
            skill_id="pipeline_smoke",
            description="Validate that runtime orchestration can execute one safe smoke skill.",
            parameters_schema={},
            preconditions=[],
            postconditions=[Condition("device_states.pipeline.smoke_completed == true")],
            allowed_backends=["noop"],
            preferred_backends=["noop"],
            rollback=RollbackSpec("pipeline_smoke_rollback", {}),
            failure_modes={},
            timeout_ms=1000,
            safety_level="low",
            irreversible=False,
        )
    }


async def run_smoke_pipeline(task_id: str = "pipeline_smoke_task") -> dict:
    outcome = await RuntimeEpisodeRunner(
        skill_library=build_smoke_skill_library(),
    ).run_skill_episode(
        StaticRuntimeEnvironmentAdapter({"noop": NoOpExecutor()}),
        SkillCall(skill_id="pipeline_smoke", params={}),
        RuntimeEpisodeSpec(task_id=task_id, data_source="smoke_pipeline"),
    )
    result = outcome.result
    return {
        "task_id": task_id,
        "runtime_entrypoint": "RuntimeEpisodeRunner.run_skill_episode",
        "state": result.state.value,
        "selected_backend": result.selected_backend,
        "reason": result.reason,
        "recovery_tier": result.recovery_tier,
        "execution_result": asdict(result.execution_result) if result.execution_result else None,
        "cognitive_map": outcome.cognitive_map.snapshot(),
    }


def run_runtime_demo_pipeline(output_dir: str | Path = "artifacts/adaptation_demo") -> dict[str, str]:
    """Run the integrated runtime-control demo and return written artifacts."""

    from evaluation.adaptation_demo import run_adaptation_demo

    return {key: str(path) for key, path in run_adaptation_demo(output_dir).items()}


def run_live_demo_pipeline(
    output_dir: str | Path = "artifacts/live_runtime_demo",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
) -> dict[str, str]:
    """Run the real Docker + Playwright runtime-control tracer bullet."""

    from evaluation.live_runtime_demo import run_live_runtime_demo

    return run_live_runtime_demo(
        output_dir,
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_base_url=wot_base_url,
        control_url=control_url,
        headless=headless,
    )


def run_live_ablation_pipeline(
    output_dir: str | Path = "artifacts/live_runtime_ablation",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
) -> dict[str, str]:
    from evaluation.live_runtime_demo import run_live_runtime_ablation

    return run_live_runtime_ablation(
        output_dir,
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_base_url=wot_base_url,
        control_url=control_url,
        headless=headless,
    )


def run_fusion_calibration_pipeline(
    output_dir: str | Path = "artifacts/live_fusion_calibration",
    *,
    dashboard_url: str = "http://127.0.0.1:3000",
    thing_directory_url: str = "http://127.0.0.1:8082/things",
    wot_base_url: str = "http://127.0.0.1:8080",
    control_url: str = "http://127.0.0.1:8081",
    headless: bool = True,
) -> dict[str, str]:
    from evaluation.live_fusion_calibration import run_live_fusion_calibration

    return run_live_fusion_calibration(
        output_dir,
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_base_url=wot_base_url,
        control_url=control_url,
        headless=headless,
    )


def run_fusion_campaign_pipeline(
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
    from evaluation.live_fusion_campaign import run_live_repeated_fusion_campaign

    return run_live_repeated_fusion_campaign(
        output_dir,
        repetitions=repetitions,
        seed_start=seed_start,
        dashboard_url=dashboard_url,
        thing_directory_url=thing_directory_url,
        wot_base_url=wot_base_url,
        control_url=control_url,
        headless=headless,
        dry_run=dry_run,
    )


def run_fusion_holdout_pipeline(
    campaign_summary_path: str | Path = "artifacts/live_fusion_campaign_full/fusion_campaign_summary.json",
    output_dir: str | Path = "artifacts/live_fusion_holdout",
    *,
    calibration_repetitions: int = 20,
    holdout_repetitions: int | None = None,
) -> dict[str, str]:
    from evaluation.fusion_holdout import write_locked_holdout_report

    return write_locked_holdout_report(
        campaign_summary_path,
        output_dir,
        calibration_repetitions=calibration_repetitions,
        holdout_repetitions=holdout_repetitions,
    )


def run_bayesian_fusion_comparator_pipeline(
    holdout_report_path: str | Path = "artifacts/live_fusion_holdout/fusion_holdout_report.json",
    output_dir: str | Path = "artifacts/bayesian_fusion_comparator",
    *,
    posterior_threshold: float = 0.5,
) -> dict[str, str]:
    from evaluation.bayesian_fusion_comparator import write_bayesian_fusion_comparator_report

    return write_bayesian_fusion_comparator_report(
        holdout_report_path,
        output_dir,
        posterior_threshold=posterior_threshold,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the modular action system pipeline.")
    parser.add_argument("--smoke", action="store_true", help="Run the current smoke orchestration path.")
    parser.add_argument("--demo", action="store_true", help="Run the white-box runtime-control demo path.")
    parser.add_argument("--live-demo", action="store_true", help="Run the Docker + Playwright live tracer bullet.")
    parser.add_argument("--live-ablation", action="store_true", help="Compare live full/no-recovery/DOM/WoT modes.")
    parser.add_argument("--fusion-calibration", action="store_true", help="Run labeled live fusion calibration.")
    parser.add_argument("--fusion-campaign", action="store_true", help="Run repeated live fusion/recovery campaign.")
    parser.add_argument("--fusion-campaign-dry-run", action="store_true", help="Write the repeated campaign plan only.")
    parser.add_argument("--fusion-holdout", action="store_true", help="Build locked calibration/holdout report.")
    parser.add_argument(
        "--bayesian-fusion-comparator",
        action="store_true",
        help="Compare experimental Bayesian posterior against locked rule-first fusion.",
    )
    parser.add_argument(
        "--campaign-summary",
        default="artifacts/live_fusion_campaign_full/fusion_campaign_summary.json",
        help="Input campaign summary for --fusion-holdout.",
    )
    parser.add_argument(
        "--holdout-report",
        default="artifacts/live_fusion_holdout/fusion_holdout_report.json",
        help="Input locked holdout report for --bayesian-fusion-comparator.",
    )
    parser.add_argument(
        "--posterior-threshold",
        type=float,
        default=0.5,
        help="Posterior blocking threshold for --bayesian-fusion-comparator.",
    )
    parser.add_argument(
        "--calibration-repetitions",
        type=int,
        default=20,
        help="Calibration repetitions per condition for locked holdout.",
    )
    parser.add_argument(
        "--holdout-repetitions",
        type=int,
        help="Holdout repetitions per condition; defaults to all remaining repetitions.",
    )
    parser.add_argument("--repetitions", type=int, default=30, help="Repeated campaign trials per condition.")
    parser.add_argument("--seed-start", type=int, default=1000, help="First deterministic campaign seed.")
    parser.add_argument("--output-dir")
    parser.add_argument("--task-id", default="pipeline_smoke_task")
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:3000")
    parser.add_argument("--thing-directory-url", default="http://127.0.0.1:8082/things")
    parser.add_argument("--wot-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--control-url", default="http://127.0.0.1:8081")
    parser.add_argument("--headed", action="store_true", help="Show Chromium for the live demo.")
    args = parser.parse_args()

    if args.bayesian_fusion_comparator:
        summary = run_bayesian_fusion_comparator_pipeline(
            args.holdout_report,
            args.output_dir or "artifacts/bayesian_fusion_comparator",
            posterior_threshold=args.posterior_threshold,
        )
    elif args.fusion_holdout:
        summary = run_fusion_holdout_pipeline(
            args.campaign_summary,
            args.output_dir or "artifacts/live_fusion_holdout",
            calibration_repetitions=args.calibration_repetitions,
            holdout_repetitions=args.holdout_repetitions,
        )
    elif args.fusion_campaign or args.fusion_campaign_dry_run:
        summary = run_fusion_campaign_pipeline(
            args.output_dir or "artifacts/live_fusion_campaign",
            repetitions=args.repetitions,
            seed_start=args.seed_start,
            dashboard_url=args.dashboard_url,
            thing_directory_url=args.thing_directory_url,
            wot_base_url=args.wot_base_url,
            control_url=args.control_url,
            headless=not args.headed,
            dry_run=args.fusion_campaign_dry_run,
        )
    elif args.fusion_calibration:
        summary = run_fusion_calibration_pipeline(
            args.output_dir or "artifacts/live_fusion_calibration",
            dashboard_url=args.dashboard_url,
            thing_directory_url=args.thing_directory_url,
            wot_base_url=args.wot_base_url,
            control_url=args.control_url,
            headless=not args.headed,
        )
    elif args.live_ablation:
        summary = run_live_ablation_pipeline(
            args.output_dir or "artifacts/live_runtime_ablation",
            dashboard_url=args.dashboard_url,
            thing_directory_url=args.thing_directory_url,
            wot_base_url=args.wot_base_url,
            control_url=args.control_url,
            headless=not args.headed,
        )
    elif args.live_demo:
        summary = run_live_demo_pipeline(
            args.output_dir or "artifacts/live_runtime_demo",
            dashboard_url=args.dashboard_url,
            thing_directory_url=args.thing_directory_url,
            wot_base_url=args.wot_base_url,
            control_url=args.control_url,
            headless=not args.headed,
        )
    elif args.demo:
        summary = run_runtime_demo_pipeline(args.output_dir or "artifacts/adaptation_demo")
    else:
        # --smoke is accepted for explicit CI usage; the default path avoids
        # live services so it stays deterministic.
        _ = args.smoke
        summary = asyncio.run(run_smoke_pipeline(task_id=args.task_id))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
