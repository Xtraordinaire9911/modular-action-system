import json

from evaluation.open_web_mock_runtime_runner import run_open_web_mock_runtime_suite
from src.pipeline import run_open_web_mock_runtime_suite_pipeline


def test_open_web_mock_runtime_suite_runs_cases_through_episode_runner(tmp_path):
    paths = run_open_web_mock_runtime_suite(tmp_path, seed_start=9000)
    report = json.loads((tmp_path / "open_web_mock_runtime_episode_report.json").read_text())
    transitions = (tmp_path / "transition_ledger.jsonl").read_text().strip().splitlines()
    failures = (tmp_path / "failure_ledger.jsonl").read_text().strip().splitlines()

    assert paths["open_web_mock_runtime_episode_report"].endswith("open_web_mock_runtime_episode_report.json")
    assert report["data_source"] == "open_web_mock_runtime_suite"
    assert report["protocol"]["runtime_entrypoint"] == "RuntimeEpisodeRunner.run_skill_episode"
    assert report["protocol"]["controlled_mock_evidence"] is True
    assert report["protocol"]["real_open_web_evidence"] is False
    assert report["summary"]["case_count"] >= 5
    assert report["summary"]["runtime_episode_count"] == report["summary"]["case_count"]
    assert report["summary"]["postcondition_failures_detected"] == report["summary"]["case_count"]
    assert report["summary"]["executor_success_count"] == report["summary"]["case_count"]
    assert report["summary"]["final_success_count"] == 0
    assert len(transitions) == report["summary"]["case_count"]
    assert len(failures) == report["summary"]["case_count"]


def test_open_web_mock_runtime_suite_pipeline_writes_report(tmp_path):
    paths = run_open_web_mock_runtime_suite_pipeline(tmp_path, seed_start=9100)
    report = json.loads((tmp_path / "open_web_mock_runtime_episode_report.json").read_text())

    assert paths["open_web_mock_runtime_episode_report"].endswith("open_web_mock_runtime_episode_report.json")
    assert report["summary"]["unique_episode_ids"] is True
    assert report["metrics"]["values"]["ExpectedEffectSuccessRate"] == 0.0
