"""Tests for the top-level pipeline entry point."""

import asyncio

from src.pipeline import run_smoke_pipeline


def test_smoke_pipeline_completes_runtime_flow():
    summary = asyncio.run(run_smoke_pipeline(task_id="test_task"))

    assert summary["task_id"] == "test_task"
    assert summary["state"] == "completed"
    assert summary["selected_backend"] == "noop"
    assert summary["execution_result"]["success"] is True
    assert summary["cognitive_map"]["device_states"]["pipeline"]["smoke_completed"] is True
