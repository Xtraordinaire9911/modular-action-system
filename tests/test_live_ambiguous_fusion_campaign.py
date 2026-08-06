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


def test_live_ambiguous_summary_can_record_gate_enabled_strategy():
    plan = build_live_ambiguous_fusion_plan(repetitions=1, seed_start=710)
    completed = [
        trial.with_result(conflict_score=0.7, detected_blocking=trial.expected_blocking, detection_latency_ms=0.2)
        for trial in plan
    ]

    summary = summarize_live_ambiguous_fusion_trials(
        completed,
        fusion_strategy="bayesian_gate",
        rule_threshold=1.0,
        posterior_threshold=0.5,
    )

    assert summary["protocol"]["fusion_strategy"] == "bayesian_gate"
    assert summary["protocol"]["production_gate_changed"] is True
    assert summary["gate"]["strategy"] == "bayesian_gate"
    assert summary["gate"]["metrics"]["miss_rate"] == 0.0
    assert summary["comparison"]["production_gate_changed"] is True
    assert summary["comparison"]["recommendation"] == "gate_enabled_evaluation_passed"


def test_live_ambiguous_profiles_use_fine_grained_fault_parameters():
    mappings = {profile.name: profile.fault_mapping() for profile in LIVE_AMBIGUOUS_PROFILES}

    assert mappings["weak_stale_signal"]["stale_offset"] == -1.5
    assert mappings["delayed_wot_recovery"]["read_delay_ms"] == 450
    assert mappings["partial_missing_wot"]["drop_probability"] == 0.7
    assert mappings["low_reliability_dom"]["source_reliability"]["dom"] < 0.5


def test_smart_room_fault_sources_expose_fine_grained_fault_hooks():
    node_source = open("env/node_wot_server/server.js", encoding="utf-8").read()
    dashboard_source = open("env/react_dashboard/src/App.jsx", encoding="utf-8").read()

    assert "read_delay_ms" in node_source
    assert "drop_probability" in node_source
    assert "drop_rng_state" in node_source
    assert "Math.random() < probability" not in node_source
    assert "source_reliability" in node_source
    assert "stale_offset" in dashboard_source
    assert "source_reliability" in dashboard_source
