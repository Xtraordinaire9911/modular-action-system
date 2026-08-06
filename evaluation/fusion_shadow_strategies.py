"""Shadow-mode fusion strategy comparison.

These helpers compare candidate fusion strategies against the production
rule-first gate without changing the production decision.  They are intentionally
side-effect free and operate on recorded trial rows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Protocol


class FusionStrategy(Protocol):
    @property
    def name(self) -> str: ...

    def score(self, trial: dict[str, Any]) -> float: ...

    def detects_blocking(self, trial: dict[str, Any]) -> bool: ...


@dataclass(frozen=True)
class RuleFirstFusionStrategy:
    """Production-equivalent fixed conflict-score threshold."""

    threshold: float = 1.0
    name: str = "rule_first_locked_threshold"

    def score(self, trial: dict[str, Any]) -> float:
        return float(trial.get("conflict_score", 0.0))

    def detects_blocking(self, trial: dict[str, Any]) -> bool:
        return self.score(trial) >= self.threshold


@dataclass(frozen=True)
class BayesianFeatureShadowStrategy:
    """Experimental posterior strategy using ambiguous-source features."""

    posterior_threshold: float = 0.5
    name: str = "bayesian_feature_shadow"

    def score(self, trial: dict[str, Any]) -> float:
        return posterior_blocking_probability(trial)

    def detects_blocking(self, trial: dict[str, Any]) -> bool:
        return self.score(trial) >= self.posterior_threshold


def compare_fusion_strategies(
    trials: Iterable[dict[str, Any]],
    *,
    production: RuleFirstFusionStrategy | None = None,
    shadows: Iterable[FusionStrategy] = (),
) -> dict[str, Any]:
    materialized = [dict(trial) for trial in trials]
    production_strategy = production or RuleFirstFusionStrategy()
    strategies: list[FusionStrategy] = [production_strategy, *list(shadows)]
    expected = [bool(trial.get("expected_blocking", False)) for trial in materialized]
    strategy_predictions = {
        strategy.name: [strategy.detects_blocking(trial) for trial in materialized] for strategy in strategies
    }
    strategy_scores = {strategy.name: [strategy.score(trial) for trial in materialized] for strategy in strategies}
    latencies = [float(trial.get("detection_latency_ms", 0.0)) for trial in materialized]
    strategy_reports = {
        strategy.name: {
            "strategy": _strategy_payload(strategy),
            "metrics": score_predictions(
                expected=expected, predicted=strategy_predictions[strategy.name], latencies=latencies
            ),
        }
        for strategy in strategies
    }
    production_balanced = float(strategy_reports[production_strategy.name]["metrics"]["balanced_accuracy"])
    shadow_deltas = {
        strategy.name: round(
            float(strategy_reports[strategy.name]["metrics"]["balanced_accuracy"]) - production_balanced,
            6,
        )
        for strategy in strategies
        if strategy.name != production_strategy.name
    }
    best_shadow = max(shadow_deltas, key=lambda strategy_name: shadow_deltas[strategy_name], default="")
    best_delta = shadow_deltas.get(best_shadow, 0.0)
    rows = []
    for index, trial in enumerate(materialized):
        production_detected = strategy_predictions[production_strategy.name][index]
        shadow_decisions = {
            strategy.name: {
                "detected_blocking": strategy_predictions[strategy.name][index],
                "score": round(strategy_scores[strategy.name][index], 6),
            }
            for strategy in strategies
            if strategy.name != production_strategy.name
        }
        rows.append(
            {
                **trial,
                "production_detected_blocking": production_detected,
                "production_decision_source": production_strategy.name,
                "shadow_decisions": shadow_decisions,
            }
        )
    return {
        "mode": "shadow_comparison",
        "production_strategy": production_strategy.name,
        "production_gate_changed": False,
        "strategy_count": len(strategies),
        "trial_count": len(materialized),
        "strategies": strategy_reports,
        "comparison": {
            "shadow_balanced_accuracy_delta": shadow_deltas,
            "best_shadow_strategy": best_shadow,
            "best_shadow_balanced_accuracy_delta": best_delta,
            "recommendation": (
                "consider_shadow_to_gate_promotion_after_independent_rerun"
                if best_delta > 0
                else "keep_rule_first_default"
            ),
        },
        "trials": rows,
    }


def posterior_blocking_probability(trial: dict[str, Any]) -> float:
    """Posterior used by the ambiguous-source Bayesian shadow strategy."""

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


def score_predictions(
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


def _strategy_payload(strategy: FusionStrategy) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": strategy.name}
    for attribute in ("threshold", "posterior_threshold"):
        if hasattr(strategy, attribute):
            payload[attribute] = getattr(strategy, attribute)
    return payload


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
