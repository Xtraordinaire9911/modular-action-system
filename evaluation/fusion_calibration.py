"""Calibration utilities for auditable rule-first sensor fusion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class FusionTrial:
    scenario: str
    expected_blocking: bool
    conflict_score: float
    detection_latency_ms: float
    source_pair: str
    conflict_type: str = ""


@dataclass(frozen=True)
class ThresholdPoint:
    threshold: float
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    true_positive_rate: float
    false_positive_rate: float
    precision: float
    balanced_accuracy: float


def calibrate_fusion_thresholds(
    trials: Iterable[FusionTrial],
    *,
    thresholds: Iterable[float] | None = None,
) -> dict[str, Any]:
    materialized = list(trials)
    candidates = list(thresholds or _candidate_thresholds(materialized))
    points = [_score_threshold(materialized, threshold) for threshold in candidates]
    recommended = max(
        points,
        key=lambda point: (
            point.balanced_accuracy,
            point.true_positive_rate,
            -point.false_positive_rate,
            point.threshold,
        ),
        default=ThresholdPoint(1.0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0),
    )
    return {
        "trial_count": len(materialized),
        "recommended_threshold": recommended.threshold,
        "recommended_operating_point": asdict(recommended),
        "threshold_curve": [asdict(point) for point in points],
        "source_confusion": _source_confusion(materialized, recommended.threshold),
        "mean_detection_latency_ms": _mean(
            [trial.detection_latency_ms for trial in materialized if trial.conflict_score >= recommended.threshold]
        ),
        "trials": [asdict(trial) for trial in materialized],
    }


def _candidate_thresholds(trials: list[FusionTrial]) -> list[float]:
    scores = sorted({round(trial.conflict_score, 6) for trial in trials})
    candidates = {0.0, 0.5, 1.0, *scores}
    candidates.update(score + 1e-6 for score in scores)
    return sorted(candidates)


def _score_threshold(trials: list[FusionTrial], threshold: float) -> ThresholdPoint:
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
    tpr = _divide(tp, tp + fn)
    fpr = _divide(fp, fp + tn)
    tnr = _divide(tn, tn + fp)
    return ThresholdPoint(
        threshold=threshold,
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
        true_positive_rate=tpr,
        false_positive_rate=fpr,
        precision=_divide(tp, tp + fp),
        balanced_accuracy=(tpr + tnr) / 2,
    )


def _source_confusion(trials: list[FusionTrial], threshold: float) -> dict[str, dict[str, int]]:
    rows: dict[str, dict[str, int]] = {}
    for trial in trials:
        row = rows.setdefault(trial.source_pair, {"expected": 0, "detected": 0, "missed": 0, "false_halt": 0})
        detected = trial.conflict_score >= threshold
        row["expected"] += int(trial.expected_blocking)
        row["detected"] += int(detected)
        row["missed"] += int(trial.expected_blocking and not detected)
        row["false_halt"] += int(not trial.expected_blocking and detected)
    return rows


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
