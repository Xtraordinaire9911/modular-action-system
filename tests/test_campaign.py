"""Repeated runs must produce metrics that can distinguish good from bad.

Recovery Tier Accuracy is undefined with one fault type, so the point of these
tests is that the aggregation actually separates a correct diagnosis from an
incorrect one, and does not quietly score an escalation as a solved task.
"""

from __future__ import annotations

from src.demos.campaign import Campaign, EpisodeResult


def _episode(**overrides) -> EpisodeResult:
    base = dict(
        scene="shop",
        fault="displace",
        expected_cause="target_moved",
        expected_tier=1,
        diagnosed_cause="target_moved",
        chosen_tier=1,
        failure_detected=True,
        goal_met=True,
        escalated=False,
    )
    base.update(overrides)
    return EpisodeResult(**base)  # type: ignore[arg-type]


# --- scoring one episode --------------------------------------------------------


def test_correct_diagnosis_and_tier_are_both_credited():
    episode = _episode()
    assert episode.diagnosis_correct and episode.tier_correct and episode.handled_well


def test_wrong_cause_is_not_credited():
    assert not _episode(diagnosed_cause="target_vanished").diagnosis_correct


def test_wrong_tier_is_not_credited():
    assert not _episode(chosen_tier=4).tier_correct


def test_escalation_counts_as_handled_only_when_escalating_was_right():
    right = _episode(
        fault="inert",
        expected_cause="action_had_no_effect",
        expected_tier=4,
        chosen_tier=4,
        goal_met=False,
        escalated=True,
    )
    wrong = _episode(goal_met=False, escalated=True)  # expected tier 1, escalated anyway

    assert right.handled_well
    assert not wrong.handled_well, "escalating out of a recoverable failure is not success"


def test_a_clean_run_is_not_scored_for_diagnosis():
    clean = _episode(
        fault="", expected_cause="", expected_tier=0, diagnosed_cause="", chosen_tier=0, failure_detected=False
    )
    assert not clean.diagnosis_correct and not clean.tier_correct
    assert clean.handled_well


# --- aggregation ----------------------------------------------------------------


def test_metrics_over_a_mixed_campaign():
    campaign = Campaign()
    for _ in range(3):
        campaign.add(_episode())  # displace, handled
        campaign.add(
            _episode(
                scene="shop2",
                fault="inert",
                expected_cause="action_had_no_effect",
                expected_tier=4,
                diagnosed_cause="action_had_no_effect",
                chosen_tier=4,
                goal_met=False,
                escalated=True,
            )
        )
        campaign.add(
            _episode(scene="clean", fault="", expected_cause="", expected_tier=0, chosen_tier=0, failure_detected=False)
        )

    metrics = campaign.metrics()
    assert metrics["episodes"] == 9 and metrics["scenes"] == 3 and metrics["repetitions"] == 3
    assert metrics["faulted_episodes"] == 6
    assert metrics["TSR"] == 1.0, "every episode was either solved or correctly escalated"
    assert metrics["RTR"] == 6 / 9
    assert metrics["RTA"] == 1.0 and metrics["DA"] == 1.0
    assert metrics["escalations"] == 3


def test_a_misdiagnosis_lowers_the_scores_it_should():
    campaign = Campaign()
    campaign.add(_episode())
    campaign.add(_episode(diagnosed_cause="target_vanished", chosen_tier=2, goal_met=False))

    metrics = campaign.metrics()
    assert metrics["DA"] == 0.5 and metrics["RTA"] == 0.5
    assert metrics["RSR"] == 0.5, "the misdiagnosed episode did not reach the goal"


def test_per_fault_breakdown_keeps_weak_spots_visible():
    campaign = Campaign()
    campaign.add(_episode())
    campaign.add(
        _episode(
            fault="vanish",
            expected_cause="target_vanished",
            expected_tier=2,
            diagnosed_cause="target_moved",
            chosen_tier=1,
            goal_met=False,
        )
    )

    rows = campaign.by_fault()
    assert rows["displace"]["DA"] == 1.0
    assert rows["vanish"]["DA"] == 0.0, "a fault the agent handles badly must not be averaged away"


def test_empty_campaign_reports_nothing_rather_than_dividing_by_zero():
    assert Campaign().metrics() == {"episodes": 0}
    assert "no episodes" in Campaign().report()


def test_report_names_every_metric_and_each_fault():
    campaign = Campaign()
    campaign.add(_episode())
    campaign.add(
        _episode(
            fault="vanish",
            expected_cause="target_vanished",
            expected_tier=2,
            diagnosed_cause="target_vanished",
            chosen_tier=2,
        )
    )

    text = campaign.report()
    for token in ("TSR", "RTR", "RSR", "RTA", "DA", "displace", "vanish"):
        assert token in text


def test_serialised_campaign_keeps_every_episode():
    campaign = Campaign()
    campaign.add(_episode())
    data = campaign.to_dict()

    assert data["metrics"]["episodes"] == 1
    assert data["episodes"][0]["expected_cause"] == "target_moved"
    assert "by_fault" in data
