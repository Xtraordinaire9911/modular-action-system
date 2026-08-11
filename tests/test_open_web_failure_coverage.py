import json

from evaluation.open_web_failure_coverage import (
    build_open_web_failure_coverage_report,
    write_open_web_failure_coverage_report,
)


def test_open_web_failure_coverage_report_tracks_mechanism_mock_and_real_levels():
    report = build_open_web_failure_coverage_report()

    assert report["data_source"] == "open_web_failure_coverage"
    assert report["summary"]["failure_class_count"] >= 8
    assert "session_auth_expiry" in report["coverage_by_class"]
    assert report["coverage_by_class"]["session_auth_expiry"]["coverage_level"] == "controlled_mock_evidence"
    assert report["summary"]["open_web_mock_case_count"] >= 5
    assert report["summary"]["controlled_browser_fixture_case_count"] >= 5
    assert report["mock_suite"]["real_open_web_evidence"] is False
    assert report["browser_fixture_suite"]["runtime_entrypoint"] == "RuntimeEpisodeRunner.run_skill_episode"
    assert report["recommendation"] == "connect_mock_cases_to_runtime_episode_runner_then_run_real_open_web_probe"


def test_open_web_failure_coverage_writer_writes_artifact(tmp_path):
    paths = write_open_web_failure_coverage_report(tmp_path)
    report = json.loads((tmp_path / "open_web_failure_coverage_report.json").read_text())

    assert paths["open_web_failure_coverage_report"].endswith("open_web_failure_coverage_report.json")
    assert report["summary"]["real_open_web_evidence_count"] == 0
