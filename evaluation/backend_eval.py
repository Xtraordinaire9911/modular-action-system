"""Backend-level evaluation harness and CLI baselines."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.contracts.types import ExecutionResult

_OUTPUT_DIR = Path("eval_outputs/backend")


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
    return {
        "mean": round(sum(values) / len(values), 3),
        "max": round(max(values), 3),
        "min": round(min(values), 3),
    }


class BackendEvaluator:
    """Aggregates Tables B1-B5 from injected executor results."""

    def __init__(self) -> None:
        self.trials: list[BackendTrial] = []

    def add(self, trial: BackendTrial) -> None:
        self.trials.append(trial)

    def add_result(self, task_id: str, result: ExecutionResult, **extra: Any) -> None:
        self.trials.append(BackendTrial.from_result(task_id, result, **extra))

    def success_rate(self, backend: str) -> float:
        rows = [trial for trial in self.trials if trial.backend == backend]
        return round(sum(trial.success for trial in rows) / len(rows), 4) if rows else 0.0

    def latency_table(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for backend in sorted({trial.backend for trial in self.trials}):
            rows = [trial for trial in self.trials if trial.backend == backend]
            ok = [trial for trial in rows if trial.success]
            lat = _stats([trial.latency_ms for trial in rows])
            out.append(
                {
                    "backend": backend,
                    "mean_latency_ms": lat["mean"],
                    "max_latency_ms": lat["max"],
                    "amortized_latency_ms": round(sum(trial.latency_ms for trial in rows) / max(len(ok), 1), 3),
                    "success_rate": self.success_rate(backend),
                    "n": len(rows),
                }
            )
        return out

    def baseline_table(self, backend: str) -> list[dict[str, Any]]:
        return [
            {
                "task_id": trial.task_id,
                "success": trial.success,
                "latency_ms": trial.latency_ms,
                "confidence": trial.confidence,
                "failure_reason": trial.failure_reason,
                **trial.extra,
            }
            for trial in self.trials
            if trial.backend == backend
        ]

    def report(self) -> dict[str, Any]:
        return {
            "summary": {backend: self.success_rate(backend) for backend in sorted({t.backend for t in self.trials})},
            "latency_table_B5": self.latency_table(),
            "dom_baseline_B1": self.baseline_table("dom"),
            "wot_baseline_B2": self.baseline_table("wot"),
            "visual_baseline_B3": self.baseline_table("visual"),
        }

    def write(self, out_dir: str | Path = _OUTPUT_DIR) -> Path:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        target = path / "backend_eval_results.json"
        target.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")
        return target


def trials_to_json(trials: list[BackendTrial]) -> str:
    return json.dumps([asdict(trial) for trial in trials], indent=2)


async def _reset_env(wot_url: str, web_url: str) -> None:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{wot_url}/api/reset")
            await client.post(f"{web_url}/api/reset")
    except Exception:
        pass


async def run_wot_baseline(wot_url: str) -> list[dict[str, Any]]:
    from src.contracts.types import Observation, SkillCall
    from src.effectors.wot_executor import WotExecutor

    td_dir = Path("config/wot_td")
    tds = [json.loads(path.read_text(encoding="utf-8")) for path in td_dir.glob("*.td.json")] if td_dir.exists() else []
    executor = WotExecutor(tds=tds)
    obs = Observation()
    skills = [
        SkillCall("turn_on_projector", {"room": "A"}),
        SkillCall("set_temperature", {"room": "A", "targetTemperature": 22}),
        SkillCall("set_lighting", {"room": "A", "brightness": 40}),
    ]

    results: list[dict[str, Any]] = []
    for skill in skills:
        await _reset_env(wot_url, "http://localhost:3000")
        result = await executor.execute(skill, obs)
        results.append(
            {
                "skill_id": skill.skill_id,
                "backend": result.backend_used,
                "success": result.success,
                "latency_ms": result.latency_ms,
                "confidence": result.confidence,
                "failure_reason": result.failure_reason,
            }
        )
    return results


def _save(name: str, data: list[dict[str, Any]]) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"saved {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dom", "wot", "visual", "all"], default="all")
    parser.add_argument("--wot-url", default="http://localhost:8080")
    args = parser.parse_args()

    if args.mode in ("wot", "all"):
        results = asyncio.run(run_wot_baseline(args.wot_url))
        _save("wot_executor_report", results)
        success_rate = sum(1 for row in results if row["success"]) / max(len(results), 1)
        print(f"WoT success rate: {success_rate:.2%}")
    if args.mode in ("dom", "all"):
        print("DOM baseline requires a live mock environment and browser session.")
    if args.mode in ("visual", "all"):
        print("Visual baseline requires Playwright and cached Set-of-Marks marks.")


if __name__ == "__main__":
    main()
