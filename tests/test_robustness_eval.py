"""Tests for the unified robustness evaluation harness."""

from evaluation.robustness_eval import run_robustness_eval


def test_level_1_delegates_to_existing_fixture_regression():
    report = run_robustness_eval(level=1)

    assert report["level"] == 1
    assert report["metrics"]["task_success_rate"] == 1.0


def test_level_2_runs_seeded_variants_and_reports_new_metrics():
    report = run_robustness_eval(level=2, seeds=[100, 101])

    assert report["level"] == 2
    assert len(report["tasks"]) == 2
    for metric in ["CSR", "OSR", "FPR", "CER", "RE"]:
        assert metric in report["metrics"]


def test_level_3_records_chaos_and_oracle_false_positive_rows():
    report = run_robustness_eval(level=3, seeds=[100, 104, 111])

    assert any(task["chaos_events"] for task in report["tasks"])
    oracle_rows = [step["oracle"] for task in report["tasks"] for step in task["steps"]]
    assert any("claimed_success" in row and "oracle_success" in row for row in oracle_rows)
    assert "FPR" in report["metrics"]
