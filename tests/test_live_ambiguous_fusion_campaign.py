from evaluation.live_ambiguous_fusion_campaign import (
    LIVE_AMBIGUOUS_PROFILES,
    build_live_ambiguous_fusion_plan,
    summarize_live_ambiguous_fusion_trials,
)


def test_live_ambiguous_plan_uses_four_profiles_with_unique_seeded_episode_ids():
    plan = build_live_ambiguous_fusion_plan(repetitions=3, seed_start=500)

    assert len(plan) == 12
    assert len({trial.episode_id for trial in plan}) == 12
    assert len({trial.seed for trial in plan}) == 12
    assert {trial.profile for trial in plan} == {profile.name for profile in LIVE_AMBIGUOUS_PROFILES}
    assert plan[0].episode_id.startswith("ambiguous_weak_stale_signal_rep_000_seed_500")
    assert all(trial.oracle_source == "ambiguous-fault-profile-label" for trial in plan)
    assert all(trial.live_fault_mapping for trial in plan)


def test_live_ambiguous_summary_reports_protocol_and_comparator_metrics():
    plan = build_live_ambiguous_fusion_plan(repetitions=1, seed_start=700)
    completed = [
        trial.with_result(conflict_score=0.7, detected_blocking=True, detection_latency_ms=0.2)
        if trial.expected_blocking
        else trial.with_result(conflict_score=0.2, detected_blocking=False, detection_latency_ms=0.2)
        for trial in plan
    ]

    summary = summarize_live_ambiguous_fusion_trials(completed, rule_threshold=1.0, posterior_threshold=0.5)

    assert summary["data_source"] == "live_ambiguous_fusion_campaign"
    assert summary["protocol"]["trial_count"] == 4
    assert summary["protocol"]["profile_counts"] == {
        "delayed_wot_recovery": 1,
        "low_reliability_dom": 1,
        "partial_missing_wot": 1,
        "weak_stale_signal": 1,
    }
    assert summary["rule_first"]["locked_threshold"] == 1.0
    assert summary["bayesian"]["posterior_threshold"] == 0.5
    assert summary["comparison"]["production_gate_changed"] is False
