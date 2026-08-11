"""Locked calibration/holdout reporting for fusion campaign evidence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from evaluation.fusion_calibration import FusionTrial, calibrate_fusion_thresholds
from evaluation.live_fusion_campaign import RepeatedFusionTrial


def build_locked_holdout_report(
    trials: Iterable[RepeatedFusionTrial],
    *,
    calibration_repetitions: int = 20,
    holdout_repetitions: int | None = None,
    thresholds: Sequence[float] | None = None,
    source_campaign_summary: str = "",
) -> dict[str, Any]:
    materialized = sorted(list(trials), key=lambda trial: (trial.scenario, trial.repetition, trial.seed))
    if calibration_repetitions <= 0:
        raise ValueError("calibration_repetitions must be positive")
    by_scenario: dict[str, list[RepeatedFusionTrial]] = {}
    for trial in materialized:
        by_scenario.setdefault(trial.scenario, []).append(trial)

    calibration: list[RepeatedFusionTrial] = []
    holdout: list[RepeatedFusionTrial] = []
    for scenario in sorted(by_scenario):
        rows = sorted(by_scenario[scenario], key=lambda trial: (trial.repetition, trial.seed))
        calibration.extend(rows[:calibration_repetitions])
        remaining = rows[calibration_repetitions:]
        holdout.extend(remaining[:holdout_repetitions] if holdout_repetitions is not None else remaining)

    calibration_report = calibrate_fusion_thresholds(
        [_fusion_trial(trial) for trial in calibration],
        thresholds=thresholds,
    )
    locked_threshold = calibration_report["recommended_threshold"]
    holdout_metrics = _score_trials(holdout, locked_threshold)
    return {
        "data_source": "live_fusion_locked_holdout",
        "source_campaign_summary": source_campaign_summary,
        "protocol": {
            "locked_after_calibration": True,
            "calibration_repetitions_per_condition": calibration_repetitions,
            "holdout_repetitions_per_condition": (
                holdout_repetitions if holdout_repetitions is not None else _min_count_by_scenario(holdout)
            ),
            "holdout_uses_calibration_threshold": True,
            "condition_count": len(by_scenario),
            "conditions": sorted(by_scenario),
            "calibration_episode_ids": [trial.episode_id for trial in calibration],
            "holdout_episode_ids": [trial.episode_id for trial in holdout],
        },
        "condition_counts": {
            "calibration": _condition_counts(calibration),
            "holdout": _condition_counts(holdout),
        },
        "calibration": {
            "trial_count": len(calibration),
            "recommended_threshold": locked_threshold,
            "recommended_operating_point": calibration_report["recommended_operating_point"],
        },
        "holdout": {
            "trial_count": len(holdout),
            "locked_threshold": locked_threshold,
            "metrics": holdout_metrics,
            "trials": [asdict(trial) for trial in holdout],
        },
    }


def write_locked_holdout_report(
    campaign_summary_path: str | Path,
    output_dir: str | Path,
    *,
    calibration_repetitions: int = 20,
    holdout_repetitions: int | None = None,
    thresholds: Sequence[float] | None = None,
) -> dict[str, str]:
    source = Path(campaign_summary_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    trials = [_trial_from_dict(row) for row in payload.get("trials", [])]
    report = build_locked_holdout_report(
        trials,
        calibration_repetitions=calibration_repetitions,
        holdout_repetitions=holdout_repetitions,
        thresholds=thresholds,
        source_campaign_summary=str(source),
    )
    report_path = target / "fusion_holdout_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"fusion_holdout_report": str(report_path)}


def _fusion_trial(trial: RepeatedFusionTrial) -> FusionTrial:
    return FusionTrial(
        scenario=trial.scenario,
        expected_blocking=trial.expected_blocking,
        conflict_score=trial.conflict_score,
        detection_latency_ms=trial.detection_latency_ms,
        source_pair=trial.source_pair,
        conflict_type=trial.conflict_type,
    )


def _score_trials(trials: list[RepeatedFusionTrial], threshold: float) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    for trial in trials:
        detected = trial.conflict_score >= threshold
        if trial.expected_blocking and detected:
            tp += 1
        elif trial.expected_blocking:
            fn += 1
        elif detected:
            fp += 1
        else:
            tn += 1
    recall = _divide(tp, tp + fn)
    specificity = _divide(tn, tn + fp)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": _divide(tp, tp + fp),
        "recall": recall,
        "false_halt_rate": _divide(fp, fp + tn),
        "miss_rate": _divide(fn, tp + fn),
        "balanced_accuracy": (recall + specificity) / 2,
        "mean_detection_latency_ms": _mean([trial.detection_latency_ms for trial in trials]),
    }


def _trial_from_dict(row: dict[str, Any]) -> RepeatedFusionTrial:
    return RepeatedFusionTrial(
        scenario=str(row["scenario"]),
        repetition=int(row["repetition"]),
        seed=int(row["seed"]),
        episode_id=str(row["episode_id"]),
        expected_blocking=bool(row["expected_blocking"]),
        detected_blocking=bool(row.get("detected_blocking", False)),
        conflict_score=float(row["conflict_score"]),
        detection_latency_ms=float(row["detection_latency_ms"]),
        source_pair=str(row.get("source_pair", "DOM+WOT")),
        reset_evidence_id=str(row.get("reset_evidence_id", "")),
        oracle_source=str(row.get("oracle_source", "")),
        conflict_type=str(row.get("conflict_type", "")),
    )


def _condition_counts(trials: list[RepeatedFusionTrial]) -> dict[str, int]:
    scenarios = sorted({trial.scenario for trial in trials})
    return {scenario: sum(1 for trial in trials if trial.scenario == scenario) for scenario in scenarios}


def _min_count_by_scenario(trials: list[RepeatedFusionTrial]) -> int:
    counts = _condition_counts(trials)
    return min(counts.values(), default=0)


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
