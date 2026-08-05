"""Tests for the top-level pipeline entry point."""

import asyncio

from src.pipeline import run_runtime_demo_pipeline, run_smoke_pipeline


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
