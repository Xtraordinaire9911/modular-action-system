import json
from pathlib import Path

from evaluation.open_web_mock_failure_suite import build_open_web_mock_failure_suite
from evaluation.open_web_playwright_fixture_runner import (
    _expected_effect_satisfied,
    run_open_web_playwright_fixture_suite,
)
from src.pipeline import run_open_web_playwright_fixture_suite_pipeline


class _FakeSession:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.actions: list[tuple[str, str, str | None]] = []
        self.closed = False

    def open(self, url: str) -> None:
        self.url = url

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector, None))

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector, value))

    def evaluate(self, expression: str, arg=None):
        _ = expression, arg
        return {"fixture_oracle_available": True}

    def screenshot(self, path: str | None = None) -> bytes:
        if path:
            Path(path).write_bytes(b"fake-png")
        return b"fake-png"

    def close(self) -> None:
        self.closed = True


def _fake_session_factory(url: str, *, headless: bool, action_timeout_ms: int):
    _ = headless, action_timeout_ms
    return _FakeSession(url)


def test_open_web_playwright_fixture_suite_uses_runtime_runner_with_browser_session_factory(tmp_path):
    paths = run_open_web_playwright_fixture_suite(
        tmp_path,
        seed_start=10000,
        session_factory=_fake_session_factory,
        capture_screenshots=True,
    )
    report = json.loads((tmp_path / "open_web_playwright_fixture_report.json").read_text())
    transitions = (tmp_path / "transition_ledger.jsonl").read_text().strip().splitlines()

    assert paths["open_web_playwright_fixture_report"].endswith("open_web_playwright_fixture_report.json")
    assert report["data_source"] == "open_web_playwright_fixture_suite"
    assert report["protocol"]["runtime_entrypoint"] == "RuntimeEpisodeRunner.run_skill_episode"
    assert report["protocol"]["browser_execution"] is True
    assert report["protocol"]["real_open_web_evidence"] is False
    assert report["summary"]["case_count"] >= 5
    assert report["summary"]["runtime_episode_count"] == report["summary"]["case_count"]
    assert report["summary"]["postcondition_failures_detected"] == report["summary"]["case_count"]
    assert len(transitions) == report["summary"]["case_count"]
    assert all(row["browser"]["url"].startswith("file://") for row in report["cases"])


def test_open_web_playwright_fixture_suite_pipeline_accepts_session_factory_for_tests(tmp_path):
    paths = run_open_web_playwright_fixture_suite_pipeline(
        tmp_path,
        seed_start=10100,
        session_factory=_fake_session_factory,
        capture_screenshots=False,
    )
    report = json.loads((tmp_path / "open_web_playwright_fixture_report.json").read_text())

    assert paths["open_web_playwright_fixture_report"].endswith("open_web_playwright_fixture_report.json")
    assert report["summary"]["executor_success_count"] == report["summary"]["case_count"]


def test_playwright_fixture_expected_effect_is_derived_from_oracle_state():
    cases = {case.case_id: case for case in build_open_web_mock_failure_suite()}

    assert _expected_effect_satisfied(cases["openweb-session-expiry"], {"profile_update_persisted": True})
    assert _expected_effect_satisfied(
        cases["openweb-autocomplete-validation"],
        {"requested_city": "New York", "submitted_city": "New York"},
    )
    assert not _expected_effect_satisfied(
        cases["openweb-autocomplete-validation"],
        {"requested_city": "New York", "submitted_city": "New York, NY"},
    )
