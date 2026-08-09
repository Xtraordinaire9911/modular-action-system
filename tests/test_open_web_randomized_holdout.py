import json

from evaluation.open_web_randomized_holdout import (
    build_open_web_failure_variants,
    build_open_web_randomized_holdout_plan,
    run_open_web_randomized_holdout_suite,
)
from tests.test_open_web_playwright_fixture_runner import _fake_session_factory


def test_variants_are_deterministic_and_cover_every_family_in_both_splits():
    dev = build_open_web_failure_variants("dev", repetitions=2)
    holdout = build_open_web_failure_variants("holdout", repetitions=2)

    assert dev == build_open_web_failure_variants("dev", repetitions=2)
    assert len(dev) == len(holdout) == 12
    assert len({variant.failure_class for variant in dev}) == 6
    assert len({variant.failure_class for variant in holdout}) == 6


def test_locked_holdout_has_disjoint_seeds_signatures_and_parameter_domains():
    plan = build_open_web_randomized_holdout_plan(dev_repetitions=3, holdout_repetitions=3)
    dev = plan["splits"]["dev"]
    holdout = plan["splits"]["holdout"]

    assert plan["protocol"]["locked_before_execution"] is True
    assert plan["protocol"]["holdout_used_for_tuning"] is False
    assert all(plan["leakage_checks"].values())
    assert {row["case"]["seed"] for row in dev}.isdisjoint({row["case"]["seed"] for row in holdout})
    assert {row["signature"] for row in dev}.isdisjoint({row["signature"] for row in holdout})

    dev_overlay = next(row for row in dev if row["case"]["case_id"] == "openweb-overlay-obstruction")
    holdout_overlay = next(row for row in holdout if row["case"]["case_id"] == "openweb-overlay-obstruction")
    assert dev_overlay["parameters"]["overlay_opacity"] < 0.7
    assert holdout_overlay["parameters"]["overlay_opacity"] > 0.7


def test_randomized_holdout_runner_persists_split_and_per_family_evidence(tmp_path):
    paths = run_open_web_randomized_holdout_suite(
        tmp_path,
        dev_repetitions=1,
        holdout_repetitions=1,
        capture_screenshots=False,
        session_factory=_fake_session_factory,
    )
    report = json.loads((tmp_path / "open_web_randomized_holdout_report.json").read_text())
    dev_report = json.loads((tmp_path / "dev" / "open_web_playwright_fixture_report.json").read_text())

    assert paths["open_web_randomized_holdout_plan"].endswith("open_web_randomized_holdout_plan.json")
    assert report["summary"]["variant_count"] == 12
    assert report["summary"]["holdout_failure_families_passed"] == 6
    assert report["summary"]["holdout_passed"] is True
    assert len(report["splits"]["dev"]["per_family"]) == 6
    assert dev_report["protocol"]["randomized_variant_evidence"] is True
    assert all(row["variant"]["split"] == "dev" for row in dev_report["cases"])
    assert all(row["variant"]["parameters"] for row in dev_report["cases"])
