"""Locked holdout for the live ambiguous fusion campaign."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from evaluation.fusion_shadow_strategies import (
    BayesianFeatureShadowStrategy,
    RuleFirstFusionStrategy,
    compare_fusion_strategies,
)


def build_live_ambiguous_locked_holdout_report(
    trials: Iterable[dict[str, Any]],
    *,
    calibration_repetitions: int = 20,
    holdout_repetitions: int | None = 10,
    rule_threshold: float = 1.0,
    posterior_threshold: float = 0.5,
    source_live_ambiguous_summary: str = "",
) -> dict[str, Any]:
    materialized = sorted(
        list(trials), key=lambda trial: (str(trial["profile"]), int(trial["repetition"]), int(trial["seed"]))
    )
    if calibration_repetitions <= 0:
        raise ValueError("calibration_repetitions must be positive")
    by_profile: dict[str, list[dict[str, Any]]] = {}
    for trial in materialized:
        by_profile.setdefault(str(trial["profile"]), []).append(dict(trial))
    calibration: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    for profile in sorted(by_profile):
        rows = sorted(by_profile[profile], key=lambda trial: (int(trial["repetition"]), int(trial["seed"])))
        calibration.extend(rows[:calibration_repetitions])
        remaining = rows[calibration_repetitions:]
        holdout.extend(remaining[:holdout_repetitions] if holdout_repetitions is not None else remaining)
    calibration_rows = [_strategy_row(row) for row in calibration]
    holdout_rows = [_strategy_row(row) for row in holdout]
    calibration_comparison = compare_fusion_strategies(
        calibration_rows,
        production=RuleFirstFusionStrategy(threshold=rule_threshold),
        shadows=[BayesianFeatureShadowStrategy(posterior_threshold=posterior_threshold)],
    )
    holdout_comparison = compare_fusion_strategies(
        holdout_rows,
        production=RuleFirstFusionStrategy(threshold=rule_threshold),
        shadows=[BayesianFeatureShadowStrategy(posterior_threshold=posterior_threshold)],
    )
    return {
        "data_source": "live_ambiguous_fusion_locked_holdout",
        "source_live_ambiguous_summary": source_live_ambiguous_summary,
        "protocol": {
            "locked_after_calibration": True,
            "production_gate_changed": False,
            "shadow_feature_source": "recorded_runtime_trial_fields",
            "live_fault_mapping_used_for_shadow_features": False,
            "observed_feature_completeness": _observed_feature_completeness(calibration + holdout),
            "calibration_repetitions_per_profile": calibration_repetitions,
            "holdout_repetitions_per_profile": (
                holdout_repetitions if holdout_repetitions is not None else _min_count_by_profile(holdout)
            ),
            "profile_count": len(by_profile),
            "profiles": sorted(by_profile),
            "calibration_episode_ids": [str(trial["episode_id"]) for trial in calibration],
            "holdout_episode_ids": [str(trial["episode_id"]) for trial in holdout],
            "rule_threshold_locked_before_holdout": rule_threshold,
            "posterior_threshold_locked_before_holdout": posterior_threshold,
        },
        "condition_counts": {
            "calibration": _profile_counts(calibration),
            "holdout": _profile_counts(holdout),
        },
        "calibration": {
            "trial_count": len(calibration),
            "strategy_comparison": calibration_comparison,
        },
        "holdout": {
            "trial_count": len(holdout),
            "rule_first": {
                "locked_threshold": rule_threshold,
                "metrics": holdout_comparison["strategies"]["rule_first_locked_threshold"]["metrics"],
            },
            "bayesian_shadow": {
                "posterior_threshold": posterior_threshold,
                "metrics": holdout_comparison["strategies"]["bayesian_feature_shadow"]["metrics"],
            },
            "comparison": {
                **holdout_comparison["comparison"],
                "bayesian_outperforms_rule_first": holdout_comparison["comparison"][
                    "best_shadow_balanced_accuracy_delta"
                ]
                > 0,
            },
            "strategy_comparison": holdout_comparison,
            "trials": holdout,
        },
    }


def write_live_ambiguous_locked_holdout_report(
    live_ambiguous_summary_path: str | Path,
    output_dir: str | Path,
    *,
    calibration_repetitions: int = 20,
    holdout_repetitions: int | None = 10,
    rule_threshold: float = 1.0,
    posterior_threshold: float = 0.5,
) -> dict[str, str]:
    source = Path(live_ambiguous_summary_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    report = build_live_ambiguous_locked_holdout_report(
        payload.get("trials", []),
        calibration_repetitions=calibration_repetitions,
        holdout_repetitions=holdout_repetitions,
        rule_threshold=rule_threshold,
        posterior_threshold=posterior_threshold,
        source_live_ambiguous_summary=str(source),
    )
    report_path = target / "live_ambiguous_fusion_holdout_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"live_ambiguous_fusion_holdout_report": str(report_path)}


def _strategy_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "scenario": row.get("profile", row.get("scenario", "")),
        "source_reliability": _observed_source_reliability(row),
        "staleness_ms": _observed_staleness(row),
        "missing_source_probability": _observed_missing_probability(row),
    }


def _observed_source_reliability(row: dict[str, Any]) -> dict[str, float]:
    reliability = row.get("source_reliability") or {}
    if isinstance(reliability, dict) and reliability:
        return {str(key): float(value) for key, value in reliability.items()}
    return {"dom": 0.5, "wot": 0.5}


def _observed_staleness(row: dict[str, Any]) -> float:
    return float(row.get("staleness_ms") or 0.0)


def _observed_missing_probability(row: dict[str, Any]) -> float:
    return float(row.get("missing_source_probability") or 0.0)


def _observed_feature_completeness(trials: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(trials)
    return {
        "trial_count": total,
        "source_reliability_present": sum(1 for trial in trials if bool(trial.get("source_reliability"))),
        "staleness_ms_present": sum(1 for trial in trials if trial.get("staleness_ms") is not None),
        "missing_source_probability_present": sum(
            1 for trial in trials if trial.get("missing_source_probability") is not None
        ),
    }


def _profile_counts(trials: list[dict[str, Any]]) -> dict[str, int]:
    profiles = sorted({str(trial["profile"]) for trial in trials})
    return {profile: sum(1 for trial in trials if str(trial["profile"]) == profile) for profile in profiles}


def _min_count_by_profile(trials: list[dict[str, Any]]) -> int:
    counts = _profile_counts(trials)
    return min(counts.values(), default=0)
