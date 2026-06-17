"""Cross-environment generalization aggregation (planning metric M1).

Member B owns backend/interface generalization. This module turns a list of
``BenchmarkRunResult`` (smart-room + external benchmarks) into the per-env and
overall Task Success Rate that evidences "the same action system works across
different environments". Output JSON feeds the final metric aggregator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.benchmarks.task_spec import BenchmarkRunResult

_OUTPUT_DIR = Path("eval_outputs/cross_env")


def aggregate(results: list[BenchmarkRunResult]) -> dict[str, Any]:
    per_env: dict[str, dict[str, Any]] = {}
    for r in results:
        bucket = per_env.setdefault(r.env, {"tasks": 0, "solved": 0, "latency_ms": []})
        bucket["tasks"] += 1
        bucket["solved"] += 1 if r.success else 0
        bucket["latency_ms"].append(r.latency_ms)

    table: list[dict[str, Any]] = []
    total = solved = 0
    for env in sorted(per_env):
        bucket = per_env[env]
        total += bucket["tasks"]
        solved += bucket["solved"]
        latencies = bucket["latency_ms"]
        table.append(
            {
                "env": env,
                "tasks": bucket["tasks"],
                "solved": bucket["solved"],
                "success_rate": round(bucket["solved"] / bucket["tasks"], 4) if bucket["tasks"] else 0.0,
                "mean_latency_ms": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            }
        )

    return {
        "per_env_M1": table,
        "overall_success_rate": round(solved / total, 4) if total else 0.0,
        "n_tasks": total,
        "n_envs": len(per_env),
    }


def write(results: list[BenchmarkRunResult], out_dir: str | Path = _OUTPUT_DIR) -> Path:
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    report = {"summary": aggregate(results), "runs": [r.to_dict() for r in results]}
    target = path / "cross_env_results.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target
