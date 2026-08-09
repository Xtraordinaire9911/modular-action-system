from evaluation.live_fusion_campaign import (
    RepeatedFusionTrial,
    build_repeated_fusion_plan,
    summarize_repeated_fusion_campaign,
)


def test_repeated_fusion_plan_builds_7_conditions_30_trials_with_unique_seeded_episode_ids():
    plan = build_repeated_fusion_plan(repetitions=30, seed_start=700)

    assert len(plan) == 210
    assert len({trial.episode_id for trial in plan}) == 210
    assert len({trial.seed for trial in plan}) == 210
    counts = {scenario: sum(1 for trial in plan if trial.scenario == scenario) for scenario in {t.scenario for t in plan}}
    assert set(counts.values()) == {30}
    assert {"clean", "stale_temperature", "wot_timeout", "wot_offline"}.issubset(counts)
    assert plan[0].episode_id.startswith("fusion_clean_rep_000_seed_700")


def test_repeated_fusion_campaign_summary_requires_reset_oracle_and_minimum_repetitions():
    trials = [
        RepeatedFusionTrial(
            scenario="clean",
            repetition=0,
            seed=1,
            episode_id="ep-clean-0",
            expected_blocking=False,
            detected_blocking=False,
            conflict_score=0.0,
            detection_latency_ms=1.0,
            source_pair="DOM+WOT",
            reset_evidence_id="reset-clean-0",
            oracle_source="fault-injection-label",
        ),
        RepeatedFusionTrial(
            scenario="stale_temperature",
            repetition=0,
            seed=2,
            episode_id="ep-stale-0",
            expected_blocking=True,
            detected_blocking=True,
            conflict_score=1.0,
            detection_latency_ms=2.0,
            source_pair="DOM+WOT",
            reset_evidence_id="reset-stale-0",
            oracle_source="fault-injection-label",
            conflict_type="value_mismatch",
        ),
    ]

    summary = summarize_repeated_fusion_campaign(
        trials,
        required_scenarios=["clean", "stale_temperature"],
        minimum_repetitions=1,
    )

    assert summary["protocol"]["trial_count"] == 2
    assert summary["protocol"]["minimum_repetitions_met"] is True
    assert summary["protocol"]["unique_episode_ids"] is True
    assert summary["protocol"]["reset_evidence_complete"] is True
    assert summary["protocol"]["independent_oracle_complete"] is True
    assert summary["condition_counts"] == {"clean": 1, "stale_temperature": 1}
    assert summary["metrics"]["recall"] == 1.0
    assert summary["metrics"]["false_halt_rate"] == 0.0
