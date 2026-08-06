from evaluation.fusion_shadow_strategies import (
    BayesianFeatureShadowStrategy,
    RuleFirstFusionStrategy,
    compare_fusion_strategies,
)


def _trial(scenario: str, expected: bool, score: float, *, missing: float = 0.0) -> dict:
    return {
        "scenario": scenario,
        "expected_blocking": expected,
        "conflict_score": score,
        "detection_latency_ms": 0.1,
        "source_reliability": {"dom": 0.55, "wot": 0.85},
        "staleness_ms": 1200.0,
        "missing_source_probability": missing,
    }


def test_shadow_strategies_compare_without_changing_production_decision():
    trials = [
        _trial("clean", False, 0.0),
        _trial("weak_stale_signal", True, 0.72),
        _trial("partial_missing_wot", True, 0.8, missing=0.7),
    ]

    report = compare_fusion_strategies(
        trials,
        production=RuleFirstFusionStrategy(threshold=1.0),
        shadows=[BayesianFeatureShadowStrategy(posterior_threshold=0.5)],
    )

    assert report["production_strategy"] == "rule_first_locked_threshold"
    assert report["production_gate_changed"] is False
    assert report["strategies"]["rule_first_locked_threshold"]["metrics"]["false_negative"] == 2
    assert report["strategies"]["bayesian_feature_shadow"]["metrics"]["false_negative"] == 0
    assert all(row["production_decision_source"] == "rule_first_locked_threshold" for row in report["trials"])
    assert all("bayesian_feature_shadow" in row["shadow_decisions"] for row in report["trials"])
