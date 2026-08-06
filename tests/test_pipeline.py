"""Tests for the top-level pipeline entry point."""

import asyncio
import json

from src.pipeline import (
    run_bayesian_fusion_comparator_pipeline,
    run_fusion_ablation_report_pipeline,
    run_fusion_campaign_pipeline,
    run_live_ambiguous_fusion_holdout_pipeline,
    run_live_ambiguous_fusion_pipeline,
    run_noisy_fusion_stress_pipeline,
    run_runtime_demo_pipeline,
    run_smoke_pipeline,
)


def test_smoke_pipeline_completes_runtime_flow():
    summary = asyncio.run(run_smoke_pipeline(task_id="test_task"))

    assert summary["task_id"] == "test_task"
    assert summary["state"] == "completed"
    assert summary["selected_backend"] == "noop"
    assert summary["runtime_entrypoint"] == "RuntimeEpisodeRunner.run_skill_episode"
    assert summary["execution_result"]["success"] is True
    assert summary["cognitive_map"]["device_states"]["pipeline"]["smoke_completed"] is True


def test_runtime_demo_pipeline_writes_artifacts(tmp_path):
    paths = run_runtime_demo_pipeline(tmp_path)

    assert "runtime_demo" in paths
    assert (tmp_path / "runtime_failure_demo.json").exists()


def test_fusion_campaign_pipeline_dry_run_writes_210_trial_plan(tmp_path):
    paths = run_fusion_campaign_pipeline(tmp_path, dry_run=True)

    plan = json.loads((tmp_path / "fusion_campaign_plan.json").read_text())
    summary = json.loads((tmp_path / "fusion_campaign_summary.json").read_text())
    assert paths["fusion_campaign_plan"].endswith("fusion_campaign_plan.json")
    assert len(plan) == 210
    assert summary["dry_run"] is True
    assert summary["planned_trial_count"] == 210


def test_bayesian_fusion_comparator_pipeline_writes_report(tmp_path):
    holdout = {
        "source_campaign_summary": "campaign.json",
        "calibration": {"recommended_threshold": 1.0, "trial_count": 2},
        "holdout": {
            "locked_threshold": 1.0,
            "trial_count": 2,
            "metrics": {"balanced_accuracy": 1.0},
            "trials": [
                {
                    "scenario": "clean",
                    "repetition": 0,
                    "seed": 1,
                    "episode_id": "clean-0",
                    "expected_blocking": False,
                    "detected_blocking": False,
                    "conflict_score": 0.0,
                    "detection_latency_ms": 1.0,
                    "source_pair": "DOM+WOT",
                    "reset_evidence_id": "reset-clean-0",
                    "oracle_source": "fault-injection-label",
                },
                {
                    "scenario": "stale_temperature",
                    "repetition": 0,
                    "seed": 2,
                    "episode_id": "stale-0",
                    "expected_blocking": True,
                    "detected_blocking": True,
                    "conflict_score": 1.0,
                    "detection_latency_ms": 1.0,
                    "source_pair": "DOM+WOT",
                    "reset_evidence_id": "reset-stale-0",
                    "oracle_source": "fault-injection-label",
                },
            ],
        },
    }
    source = tmp_path / "fusion_holdout_report.json"
    source.write_text(json.dumps(holdout), encoding="utf-8")

    paths = run_bayesian_fusion_comparator_pipeline(source, tmp_path / "bayesian")

    assert paths["bayesian_fusion_comparator_report"].endswith("bayesian_fusion_comparator_report.json")


def test_noisy_fusion_stress_pipeline_writes_synthetic_report(tmp_path):
    paths = run_noisy_fusion_stress_pipeline(tmp_path, repetitions=2, seed_start=10)
    report = json.loads((tmp_path / "noisy_fusion_stress_report.json").read_text())

    assert paths["noisy_fusion_stress_report"].endswith("noisy_fusion_stress_report.json")
    assert report["protocol"]["synthetic_not_live"] is True
    assert report["protocol"]["trial_count"] == 8


def test_live_ambiguous_fusion_pipeline_dry_run_writes_profile_plan(tmp_path):
    paths = run_live_ambiguous_fusion_pipeline(tmp_path, repetitions=2, seed_start=20, dry_run=True)
    summary = json.loads((tmp_path / "live_ambiguous_fusion_summary.json").read_text())

    assert paths["live_ambiguous_fusion_plan"].endswith("live_ambiguous_fusion_plan.json")
    assert summary["dry_run"] is True
    assert summary["planned_trial_count"] == 8
    assert summary["fine_grained_fault_api"] is True


def test_live_ambiguous_holdout_pipeline_writes_shadow_report(tmp_path):
    from evaluation.live_ambiguous_fusion_campaign import build_live_ambiguous_fusion_plan

    trials = []
    for trial in build_live_ambiguous_fusion_plan(repetitions=30, seed_start=40):
        score = 0.72 if trial.expected_blocking else 0.0
        trials.append(
            {
                **trial.__dict__,
                "detected_blocking": score >= 1.0,
                "conflict_score": score,
                "detection_latency_ms": 0.1,
                "reset_evidence_id": f"reset-{trial.episode_id}",
            }
        )
    source = tmp_path / "live_ambiguous_fusion_summary.json"
    source.write_text(json.dumps({"trials": trials}), encoding="utf-8")

    paths = run_live_ambiguous_fusion_holdout_pipeline(source, tmp_path / "holdout")
    report = json.loads((tmp_path / "holdout" / "live_ambiguous_fusion_holdout_report.json").read_text())

    assert paths["live_ambiguous_fusion_holdout_report"].endswith("live_ambiguous_fusion_holdout_report.json")
    assert report["holdout"]["strategy_comparison"]["production_gate_changed"] is False


def test_fusion_ablation_report_pipeline_writes_report(tmp_path):
    holdout = {
        "holdout": {
            "trial_count": 1,
            "strategy_comparison": {
                "production_strategy": "rule_first_locked_threshold",
                "production_gate_changed": False,
                "strategies": {
                    "rule_first_locked_threshold": {"metrics": {"balanced_accuracy": 0.5}},
                    "bayesian_feature_shadow": {"metrics": {"balanced_accuracy": 1.0}},
                },
                "comparison": {
                    "best_shadow_strategy": "bayesian_feature_shadow",
                    "best_shadow_balanced_accuracy_delta": 0.5,
                    "recommendation": "consider_shadow_to_gate_promotion_after_independent_rerun",
                },
            },
        }
    }
    source = tmp_path / "holdout.json"
    source.write_text(json.dumps(holdout), encoding="utf-8")

    paths = run_fusion_ablation_report_pipeline(source, tmp_path / "ablation")

    assert paths["bayesian_vs_rule_first_ablation_report"].endswith(
        "bayesian_vs_rule_first_ablation_report.json"
    )
