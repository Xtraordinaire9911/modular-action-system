"""Every reported metric must be recomputable from the tallies shown beside it.

The point of the ledger is that a reader can check a figure by hand. So the
tests care that the working is stated, that it matches the arithmetic, and that
an empty run reports zero rather than dividing by zero.
"""

from __future__ import annotations

from src.demos.ledger import Counters, MetricLedger


def _run_one_episode(ledger: MetricLedger, *, fails: bool, recovers: bool = False, escalates: bool = False) -> None:
    ledger.observed(elements=7)
    ledger.measured(boxes=7)
    ledger.scored(candidates=7)
    ledger.acted()
    ledger.verified(passed=not fails)
    if fails:
        ledger.probed(3)
        ledger.diagnosed("target_moved", 1)
        if recovers:
            ledger.recovered()
            ledger.verified(passed=True)
        if escalates:
            ledger.escalated()
    ledger.episode_done(goal_met=(not fails) or recovers)


# --- the tallies ----------------------------------------------------------------


def test_a_clean_episode_records_the_expected_counts():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=False)
    c = ledger.counters

    assert c.observations == 1 and c.elements_seen == 7 and c.elements_measured == 7
    assert c.actions == 1 and c.verifications == 1 and c.verify_passed == 1
    assert c.diagnoses == 0 and c.episodes == 1 and c.goals_met == 1


def test_a_recovered_episode_counts_two_verifications():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, recovers=True)
    c = ledger.counters

    assert c.verifications == 2 and c.verify_failed == 1 and c.verify_passed == 1
    assert c.diagnoses == 1 and c.recoveries == 1 and c.goals_met == 1


def test_an_escalated_episode_is_not_counted_as_a_goal_met():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, escalates=True)

    assert ledger.counters.escalations == 1
    assert ledger.counters.goals_met == 0, "handing over is not the same as succeeding"


# --- the arithmetic is stated, not just the answer -------------------------------


def test_the_ledger_does_not_reuse_the_campaign_metric_names():
    """The campaign scores a correct handover as handled; this counts goals reached.

    Publishing both under "TSR" would show two different numbers for one name.
    """
    names = {name for name, _, _ in MetricLedger().derivations()}
    assert not (names & {"TSR", "RTR", "RSR", "RTA", "DA"})


def test_every_metric_states_the_division_it_performed():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=False)
    _run_one_episode(ledger, fails=True, recovers=True)

    for name, working, value in ledger.derivations():
        assert working, f"{name} reports a value with no working"
        assert "/" in working, f"{name} should show the division it performed"
        assert 0.0 <= value <= 1.0


def test_the_stated_working_matches_the_value():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=False)
    _run_one_episode(ledger, fails=True, recovers=True)
    _run_one_episode(ledger, fails=True, escalates=True)

    rows = {name: value for name, _, value in ledger.derivations()}
    c = ledger.counters

    assert rows["goal reached"] == c.goals_met / c.episodes == 2 / 3
    assert rows["failure detected"] == c.verify_failed / c.episodes == 2 / 3
    assert rows["recovery attempted"] == c.recoveries / c.verify_failed == 1 / 2
    assert rows["handed over"] == c.escalations / c.verify_failed == 1 / 2


def test_an_empty_ledger_reports_zero_rather_than_failing():
    ledger = MetricLedger()
    assert all(value == 0.0 for _, _, value in ledger.derivations())
    assert "goal reached" in ledger.report()


# --- the compact strip ------------------------------------------------------------


def test_the_strip_is_one_short_line_covering_the_loop():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, recovers=True)
    strip = ledger.counters.as_strip()

    assert "\n" not in strip, "the strip must stay a single quiet line"
    for token in ("obs", "seen", "meas", "cand", "act", "ver", "probe", "diag", "rec", "esc"):
        assert token in strip
    assert len(strip) < 130


def test_the_strip_shows_verifications_as_passed_over_total():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, recovers=True)
    assert "ver 1/2" in ledger.counters.as_strip()


# --- the record -------------------------------------------------------------------


def test_serialised_ledger_keeps_counters_working_and_notes():
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, recovers=True)
    data = ledger.to_dict()

    assert data["counters"]["episodes"] == 1
    assert any(row["metric"] == "goal reached" and "working" in row for row in data["derivations"])
    assert data["notes"] and "target_moved" in data["notes"][0]


def test_counters_default_to_zero():
    assert Counters().as_strip().startswith("obs 0")


def test_the_strip_is_plain_ascii():
    """It is printed to a terminal too, and a regional code page may not encode more."""
    ledger = MetricLedger()
    _run_one_episode(ledger, fails=True, recovers=True)
    strip = ledger.counters.as_strip()

    assert strip.isascii(), f"non-ascii in the strip: {[c for c in strip if not c.isascii()]}"
    assert ledger.report().isascii()
