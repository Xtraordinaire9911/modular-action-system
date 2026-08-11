"""Bayesian-vs-rule-first fusion ablation report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_fusion_ablation_report(holdout_report: dict[str, Any]) -> dict[str, Any]:
    comparison = holdout_report.get("holdout", {}).get("strategy_comparison", {})
    strategies = comparison.get("strategies", {})
    production = str(comparison.get("production_strategy", "rule_first_locked_threshold"))
    best_shadow = str(comparison.get("comparison", {}).get("best_shadow_strategy", ""))
    return {
        "data_source": "bayesian_vs_rule_first_fusion_ablation",
        "mode": "shadow_ablation",
        "source_holdout_report": "",
        "source_live_ambiguous_summary": holdout_report.get("source_live_ambiguous_summary", ""),
        "production_strategy": production,
        "shadow_strategy": best_shadow,
        "production_gate_changed": bool(comparison.get("production_gate_changed", False)),
        "trial_count": int(holdout_report.get("holdout", {}).get("trial_count", 0)),
        "metrics": {
            "production": strategies.get(production, {}).get("metrics", {}),
            "shadow": strategies.get(best_shadow, {}).get("metrics", {}) if best_shadow else {},
        },
        "comparison": comparison.get("comparison", {}),
        "recommendation": comparison.get("comparison", {}).get("recommendation", "keep_rule_first_default"),
        "boundary": {
            "production_default_unchanged": True,
            "shadow_mode_only": True,
            "promotion_requires": [
                "independent live ambiguous rerun",
                "locked holdout without post-hoc tuning",
                "false-halt and miss-rate review",
                "CIM/verifier integration review before gate replacement",
            ],
        },
    }


def write_fusion_ablation_report(holdout_report_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    source = Path(holdout_report_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    holdout_report = json.loads(source.read_text(encoding="utf-8"))
    report = build_fusion_ablation_report(holdout_report)
    report["source_holdout_report"] = str(source)
    report_path = target / "bayesian_vs_rule_first_ablation_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"bayesian_vs_rule_first_ablation_report": str(report_path)}
