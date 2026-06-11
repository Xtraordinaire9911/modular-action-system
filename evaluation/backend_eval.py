"""Backend-level evaluation harness (Member B — produces Tables B1–B5).

Records one ``BackendTrial`` per (task, backend) execution and aggregates the
backend-ownership metrics: DOM/WoT/Visual success rates, mean/max/amortized
latency, and the robustness matrix (which failure type produced which observed
behaviour). It is executor-agnostic: real executors or fakes are injected, so
the harness unit-tests offline and runs against the live env unchanged.

Outputs JSON (`eval_outputs/backend/*.json`) consumed by Member C's final
metric aggregator.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.contracts.types import ExecutionResult


@dataclass
class BackendTrial:
    task_id: str
    backend: str
    success: bool
    latency_ms: float
    confidence: float = 0.0
    failure_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, task_id: str, result: ExecutionResult, **extra: Any) -> "BackendTrial":
        return cls(
            task_id=task_id,
            backend=result.backend_used,
            success=result.success,
            latency_ms=result.latency_ms,
            confidence=result.confidence,
            failure_reason=result.failure_reason,
            extra=extra,
        )


def _stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "max": 0.0, "min": 0.0}
    return {"mean": round(sum(values) / len(values), 3), "max": round(max(values), 3), "min": round(min(values), 3)}


class BackendEvaluator:
    def __init__(self) -> None:
        self.trials: list[BackendTrial] = []

    def add(self, trial: BackendTrial) -> None:
        self.trials.append(trial)

    def add_result(self, task_id: str, result: ExecutionResult, **extra: Any) -> None:
        self.trials.append(BackendTrial.from_result(task_id, result, **extra))

    # ── per-backend success / latency ────────────────────────────────────────
    def success_rate(self, backend: str) -> float:
        rows = [t for t in self.trials if t.backend == backend]
        return round(sum(t.success for t in rows) / len(rows), 4) if rows else 0.0

    def latency_table(self) -> list[dict[str, Any]]:
        """Table B5: per-backend mean/max + amortized latency over successes."""
        out = []
        for backend in sorted({t.backend for t in self.trials}):
            rows = [t for t in self.trials if t.backend == backend]
            ok = [t for t in rows if t.success]
            lat = _stats([t.latency_ms for t in rows])
            out.append(
                {
                    "backend": backend,
                    "mean_latency_ms": lat["mean"],
                    "max_latency_ms": lat["max"],
                    "amortized_latency_ms": round(sum(t.latency_ms for t in rows) / max(len(ok), 1), 3),
                    "success_rate": self.success_rate(backend),
                    "n": len(rows),
                }
            )
        return out

    # ── per-backend baseline tables (B1/B2/B3) ───────────────────────────────
    def baseline_table(self, backend: str) -> list[dict[str, Any]]:
        return [
            {
                "task_id": t.task_id,
                "success": t.success,
                "latency_ms": t.latency_ms,
                "confidence": t.confidence,
                "failure_reason": t.failure_reason,
                **t.extra,
            }
            for t in self.trials
            if t.backend == backend
        ]

    def report(self) -> dict[str, Any]:
        return {
            "summary": {b: self.success_rate(b) for b in sorted({t.backend for t in self.trials})},
            "latency_table_B5": self.latency_table(),
            "dom_baseline_B1": self.baseline_table("dom"),
            "wot_baseline_B2": self.baseline_table("wot"),
            "visual_baseline_B3": self.baseline_table("visual"),
        }

    def write(self, out_dir: str | Path = "eval_outputs/backend") -> Path:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        target = path / "backend_eval_results.json"
        target.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")
        return target


def trials_to_json(trials: list[BackendTrial]) -> str:
    return json.dumps([asdict(t) for t in trials], indent=2)
