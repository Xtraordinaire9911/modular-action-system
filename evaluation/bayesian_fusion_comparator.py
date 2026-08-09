"""Experimental Bayesian comparator for fusion holdout evidence.

This module deliberately does not replace the production fusion gate. It reads a
locked holdout report, applies a simple calibrated posterior model to the same
holdout trials, and reports whether the Bayesian comparator improves on the
locked rule-first threshold.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class BayesianFusionModel:
    """Logistic posterior over blocking given an observed conflict score."""

    prior_blocking: float = 0.5
    score_midpoint: float = 1.0
    score_scale: float = 8.0
    posterior_threshold: float = 0.5

    def posterior_blocking_probability(self, trial: dict[str, Any]) -> float:
        score = float(trial.get("conflict_score", 0.0))
        logit_prior = _logit(self.prior_blocking)
        likelihood_logit = self.score_scale * (score - self.score_midpoint)
        return _sigmoid(logit_prior + likelihood_logit)

    def detects_blocking(self, trial: dict[str, Any]) -> bool:
        return self.posterior_blocking_probability(trial) >= self.posterior_threshold


def build_bayesian_fusion_comparator_report(
    holdout_report: dict[str, Any],
    *,
    posterior_threshold: float = 0.5,
) -> dict[str, Any]:
    calibration_trials = holdout_report.get("calibration", {}).get("trial_count", 0)
    holdout_trials = list(holdout_report.get("holdout", {}).get("trials", []))
    locked_threshold = float(holdout_report.get("holdout", {}).get("locked_threshold", 1.0))
    model = _fit_model_from_holdout_report(holdout_report, posterior_threshold=posterior_threshold)
    bayesian_rows = [
        {
            **trial,
            "posterior_blocking_probability": round(model.posterior_blocking_probability(trial), 6),
            "bayesian_detected_blocking": model.detects_blocking(trial),
        }
        for trial in holdout_trials
    ]
    bayesian_metrics = _score_predictions(
        expected=[bool(trial.get("expected_blocking", False)) for trial in holdout_trials],
        predicted=[bool(row["bayesian_detected_blocking"]) for row in bayesian_rows],
        latencies=[float(trial.get("detection_latency_ms", 0.0)) for trial in holdout_trials],
    )
    rule_metrics = dict(holdout_report.get("holdout", {}).get("metrics", {}))
    rule_balanced = float(rule_metrics.get("balanced_accuracy", 0.0))
    bayesian_balanced = float(bayesian_metrics.get("balanced_accuracy", 0.0))
    bayesian_outperforms = bayesian_balanced > rule_balanced
    return {
        "data_source": "live_fusion_bayesian_comparator",
        "mode": "experimental_comparator",
        "production_default": "rule_first_locked_threshold",
        "source_holdout_report": "",
        "source_campaign_summary": holdout_report.get("source_campaign_summary", ""),
        "training_boundary": {
            "calibration_trials": calibration_trials,
            "holdout_trials": len(holdout_trials),
            "bayesian_model_fitted_from": "calibration score range only",
            "holdout_used_for_tuning": False,
        },
        "rule_first": {
            "locked_threshold": locked_threshold,
            "metrics": rule_metrics,
        },
        "bayesian": {
            "model": asdict(model),
            "posterior_threshold": model.posterior_threshold,
            "metrics": bayesian_metrics,
            "trials": bayesian_rows,
        },
        "comparison": {
            "balanced_accuracy_delta": round(bayesian_balanced - rule_balanced, 6),
            "bayesian_outperforms_rule_first": bayesian_outperforms,
            "recommendation": "consider_bayesian_gate" if bayesian_outperforms else "keep_rule_first_default",
        },
    }


def write_bayesian_fusion_comparator_report(
    holdout_report_path: str | Path,
    output_dir: str | Path,
    *,
    posterior_threshold: float = 0.5,
) -> dict[str, str]:
    source = Path(holdout_report_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    holdout_report = json.loads(source.read_text(encoding="utf-8"))
    report = build_bayesian_fusion_comparator_report(
        holdout_report,
        posterior_threshold=posterior_threshold,
    )
    report["source_holdout_report"] = str(source)
    report_path = target / "bayesian_fusion_comparator_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"bayesian_fusion_comparator_report": str(report_path)}


def _fit_model_from_holdout_report(
    holdout_report: dict[str, Any],
    *,
    posterior_threshold: float,
) -> BayesianFusionModel:
    calibration_point = holdout_report.get("calibration", {}).get("recommended_operating_point", {})
    midpoint = float(holdout_report.get("calibration", {}).get("recommended_threshold", 1.0))
    balanced = float(calibration_point.get("balanced_accuracy", 1.0))
    # Keep the model conservative when calibration is weak; high calibration
    # separation makes the posterior steep around the locked threshold.
    scale = 2.0 + 6.0 * max(0.0, min(1.0, balanced))
    return BayesianFusionModel(score_midpoint=midpoint, score_scale=scale, posterior_threshold=posterior_threshold)


def _score_predictions(
    *,
    expected: Iterable[bool],
    predicted: Iterable[bool],
    latencies: Iterable[float],
) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    for truth, guess in zip(expected, predicted, strict=True):
        if truth and guess:
            tp += 1
        elif truth:
            fn += 1
        elif guess:
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
        "mean_detection_latency_ms": _mean(list(latencies)),
    }


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def _logit(probability: float) -> float:
    clipped = min(max(probability, 1e-6), 1 - 1e-6)
    return math.log(clipped / (1 - clipped))


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
