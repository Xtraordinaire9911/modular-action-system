import json

from evaluation.open_web_failure_coverage import build_open_web_failure_coverage_report, write_open_web_failure_coverage_report


def test_open_web_failure_coverage_report_tracks_mechanism_mock_and_real_levels():
    report = build_open_web_failure_coverage_report()

    assert report["data_source"] == "open_web_failure_coverage"
    assert report["summary"]["failure_class_count"] >= 8
    assert "session_auth_expiry" in report["coverage_by_class"]
    assert report["coverage_by_class"]["session_auth_expiry"]["coverage_level"] == "mechanism_ready"
    assert report["recommendation"] == "build_mock_then_real_open_web_evidence"


def test_open_web_failure_coverage_writer_writes_artifact(tmp_path):
    paths = write_open_web_failure_coverage_report(tmp_path)
    report = json.loads((tmp_path / "open_web_failure_coverage_report.json").read_text())

    assert paths["open_web_failure_coverage_report"].endswith("open_web_failure_coverage_report.json")
    assert report["summary"]["real_open_web_evidence_count"] == 0
