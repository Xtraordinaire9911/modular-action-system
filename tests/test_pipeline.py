"""Tests for the scaffold pipeline entry point."""

from src.pipeline import run_scaffold_smoke


def test_scaffold_pipeline_entry_point_is_runnable():
    summary = run_scaffold_smoke(task_id="test_task")

    assert summary["task_id"] == "test_task"
    assert summary["state"] == "completed"
    assert summary["selected_backend"] == "scaffold"
    assert summary["execution_result"]["success"] is True
