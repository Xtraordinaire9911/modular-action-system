"""Tests for the top-level pipeline entry point."""

import asyncio
import json

from src.pipeline import run_fusion_campaign_pipeline, run_runtime_demo_pipeline, run_smoke_pipeline


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
