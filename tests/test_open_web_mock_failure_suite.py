import json
from pathlib import Path

from evaluation.open_web_mock_failure_suite import (
    build_open_web_mock_failure_suite,
    write_open_web_mock_failure_suite_report,
)
from src.pipeline import run_open_web_mock_failure_suite_pipeline


def test_open_web_mock_suite_defines_oracle_labeled_cases():
    cases = build_open_web_mock_failure_suite(seed_start=8000)

    assert len(cases) >= 5
    assert {case.failure_class for case in cases} >= {
        "overlay_modal_obstruction",
        "session_auth_expiry",
        "autocomplete_async_validation_mutation",
        "optimistic_ui_backend_mismatch",
        "dom_vs_visual_disagreement",
    }
    assert all(case.coverage_level == "controlled_mock_evidence" for case in cases)
    assert all(case.real_open_web is False for case in cases)
    assert all(case.oracle_state for case in cases)
    assert all(case.expected_runtime_response for case in cases)
    assert len({case.episode_id for case in cases}) == len(cases)
    assert len({case.seed for case in cases}) == len(cases)


def test_open_web_mock_html_fixtures_exist_with_oracle_markers():
    for case in build_open_web_mock_failure_suite():
        fixture = Path("env/mock_envs") / case.html_fixture

        assert fixture.exists(), case.case_id
        html = fixture.read_text(encoding="utf-8")
        assert "data-oracle-state" in html
        assert case.case_id in html


def test_open_web_mock_suite_writer_writes_plan_and_report(tmp_path):
    paths = write_open_web_mock_failure_suite_report(tmp_path, seed_start=8100)
    report = json.loads((tmp_path / "open_web_mock_failure_suite_report.json").read_text())
    plan = json.loads((tmp_path / "open_web_mock_failure_plan.json").read_text())

    assert paths["open_web_mock_failure_suite_report"].endswith("open_web_mock_failure_suite_report.json")
    assert paths["open_web_mock_failure_plan"].endswith("open_web_mock_failure_plan.json")
    assert report["data_source"] == "open_web_mock_failure_suite"
    assert report["protocol"]["controlled_mock_evidence"] is True
    assert report["protocol"]["real_open_web_evidence"] is False
    assert report["summary"]["case_count"] == len(plan)
    assert report["summary"]["real_open_web_evidence_count"] == 0
    assert report["summary"]["controlled_mock_evidence_count"] == len(plan)


def test_open_web_mock_failure_suite_pipeline_writes_artifacts(tmp_path):
    paths = run_open_web_mock_failure_suite_pipeline(tmp_path, seed_start=8200)

    assert paths["open_web_mock_failure_suite_report"].endswith("open_web_mock_failure_suite_report.json")
    assert (tmp_path / "open_web_mock_failure_plan.json").exists()
