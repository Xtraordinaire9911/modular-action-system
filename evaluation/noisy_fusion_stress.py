"""Synthetic ambiguous/noisy fusion stress set for Bayesian comparator work.

The live 30×7 campaign is intentionally clean and rule-first already saturates
it. This module creates controlled, labeled stress cases where evidence is
ambiguous enough that a posterior-style comparator can be meaningfully tested.
It is not live evidence and must not be reported as production behavior.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Iterable

ORACLE_SOURCE = "synthetic-noisy-source-oracle"


def generate_noisy_fusion_trials(*, repetitions: int = 30, seed_start: int = 3000) -> list[dict[str, Any]]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    scenarios = [
        ("weak_stale_signal", True, 0.72, 0.55, 0.85, 1200.0, 0.2),
        ("delayed_wot_recovery", True, 0.65, 0.6, 0.9, 900.0, 0.1),
        ("low_reliability_dom", False, 0.55, 0.25, 0.95, 120.0, 0.0),
        ("partial_missing_wot", True, 0.8, 0.7, 0.35, 500.0, 0.7),
    ]
    rows: list[dict[str, Any]] = []
    seed = seed_start
    for scenario, expected, base_score, dom_reliability, wot_reliability, stale_ms, missing_prob in scenarios:
        for repetition in range(repetitions):
            rng = random.Random(seed)
            score = max(0.0, min(1.2, base_score + rng.uniform(-0.08, 0.08)))
            rows.append(
                {
                    "scenario": scenario,
                    "repetition": repetition,
                    "seed": seed,
                    "episode_id": f"noisy_{scenario}_rep_{repetition:03d}_seed_{seed}",
                    "expected_blocking": expected,
                    "conflict_score": round(score, 4),
                    "detection_latency_ms": round(0.2 + rng.random() * 0.2, 4),
                    "source_pair": "DOM+WOT",
                    "source_reliability": {
                        "dom": round(max(0.0, min(1.0, dom_reliability + rng.uniform(-0.05, 0.05))), 3),
                        "wot": round(max(0.0, min(1.0, wot_reliability + rng.uniform(-0.05, 0.05))), 3),
                    },
                    "staleness_ms": round(max(0.0, stale_ms + rng.uniform(-80.0, 80.0)), 3),
                    "missing_source_probability": round(max(0.0, min(1.0, missing_prob + rng.uniform(-0.05, 0.05))), 3),
                    "oracle_source": ORACLE_SOURCE,
                    "noise_profile": "ambiguous_source_reliability_and_staleness",
                }
            )
            seed += 1
    return rows


def build_noisy_fusion_stress_report(
    trials: Iterable[dict[str, Any]],
    *,
    rule_threshold: float = 1.0,
    posterior_threshold: float = 0.5,
) -> dict[str, Any]:
    materialized = list(trials)
    rule_predictions = [float(trial["conflict_score"]) >= rule_threshold for trial in materialized]
    bayesian_rows = [
        {
            **trial,
            "posterior_blocking_probability": round(_posterior_blocking_probability(trial), 6),
        }
        for trial in materialized
    ]
    bayesian_predictions = [
        float(trial["posterior_blocking_probability"]) >= posterior_threshold for trial in bayesian_rows
    ]
    expected = [bool(trial["expected_blocking"]) for trial in materialized]
    rule_metrics = _score_predictions(expected, rule_predictions, materialized)
    bayesian_metrics = _score_predictions(expected, bayesian_predictions, materialized)
    delta = float(bayesian_metrics["balanced_accuracy"]) - float(rule_metrics["balanced_accuracy"])
    return {
        "data_source": "synthetic_noisy_fusion_stress",
        "protocol": {
            "trial_count": len(materialized),
            "oracle_source": ORACLE_SOURCE,
            "synthetic_not_live": True,
            "purpose": "stress Bayesian comparator on ambiguous/noisy source features before any runtime gate integration",
            "condition_counts": _condition_counts(materialized),
        },
        "rule_first": {
            "locked_threshold": rule_threshold,
            "metrics": rule_metrics,
        },
        "bayesian": {
            "posterior_threshold": posterior_threshold,
            "feature_likelihoods": [
                "conflict_score",
                "source_reliability.dom",
                "source_reliability.wot",
                "staleness_ms",
                "missing_source_probability",
            ],
            "metrics": bayesian_metrics,
            "trials": bayesian_rows,
        },
        "comparison": {
            "balanced_accuracy_delta": round(delta, 6),
            "bayesian_outperforms_rule_first": delta > 0,
            "recommendation": (
                "evaluate_on_live_ambiguous_cases_before_gate_integration" if delta > 0 else "keep_rule_first_default"
            ),
        },
    }


def write_noisy_fusion_stress_report(
    output_dir: str | Path = "artifacts/noisy_fusion_stress",
    *,
    repetitions: int = 30,
    seed_start: int = 3000,
    rule_threshold: float = 1.0,
    posterior_threshold: float = 0.5,
) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    trials = generate_noisy_fusion_trials(repetitions=repetitions, seed_start=seed_start)
    report = build_noisy_fusion_stress_report(
        trials,
        rule_threshold=rule_threshold,
        posterior_threshold=posterior_threshold,
    )
    path = target / "noisy_fusion_stress_report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"noisy_fusion_stress_report": str(path)}


def _posterior_blocking_probability(trial: dict[str, Any]) -> float:
    score = float(trial.get("conflict_score", 0.0))
    reliability = trial.get("source_reliability", {})
    dom_reliability = float(reliability.get("dom", 0.5))
    wot_reliability = float(reliability.get("wot", 0.5))
    staleness_ms = float(trial.get("staleness_ms", 0.0))
    missing_probability = float(trial.get("missing_source_probability", 0.0))
    logit = (
        -2.2
        + 2.8 * score
        + 2.0 * missing_probability
        + 1.2 * min(staleness_ms / 1000.0, 2.0)
        + 1.4 * (1.0 - wot_reliability)
        - 0.8 * max(0.0, wot_reliability - dom_reliability)
    )
    return _sigmoid(logit)


def _score_predictions(
    expected: list[bool],
    predicted: list[bool],
    trials: list[dict[str, Any]],
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
        "mean_detection_latency_ms": _mean([float(trial.get("detection_latency_ms", 0.0)) for trial in trials]),
    }


def _condition_counts(trials: list[dict[str, Any]]) -> dict[str, int]:
    scenarios = sorted({str(trial["scenario"]) for trial in trials})
    return {scenario: sum(1 for trial in trials if trial["scenario"] == scenario) for scenario in scenarios}


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def _divide(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
