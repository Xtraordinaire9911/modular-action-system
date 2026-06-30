"""Tests for the offline fixture evaluation harness."""

from evaluation.fixture_eval import evaluate_all_task_fixtures


def test_fixture_eval_reports_core_metrics_and_tasks():
    report = evaluate_all_task_fixtures()

    assert "tasks" in report
    assert "metrics" in report
    assert len(report["tasks"]) == 4

    metrics = report["metrics"]
    assert "task_success_rate" in metrics
    assert "skill_sequence_match_rate" in metrics
    assert "recovery_tier_match_rate" in metrics
    assert "TSR" in metrics
    assert metrics["task_success_rate"] == 1.0
    assert metrics["skill_sequence_match_rate"] == 1.0
    assert metrics["recovery_tier_match_rate"] == 1.0
