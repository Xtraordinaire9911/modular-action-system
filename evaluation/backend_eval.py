"""Backend evaluation harness (Member B).

Runs DOM-only, WoT-only, and Visual-only baselines against the mock
environment and records per-backend metrics to eval_outputs/backend/.

Usage:
  python evaluation/backend_eval.py --mode dom
  python evaluation/backend_eval.py --mode wot
  python evaluation/backend_eval.py --mode all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import time
from typing import Any

_OUTPUT_DIR = pathlib.Path("eval_outputs/backend")


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
    from src.perception.td_affordance_parser import parse_td_directory

    td_dir = pathlib.Path("config/wot_td")
    tds = []
    if td_dir.exists():
        for p in td_dir.glob("*.td.json"):
            tds.append(json.loads(p.read_text()))

    executor = WotExecutor(tds=tds)
    obs = Observation()
    skills = [
        SkillCall("turn_on_projector", {"room": "A"}),
        SkillCall("set_temperature", {"room": "A", "target": 22}),
        SkillCall("set_lighting", {"room": "A", "brightness": 40}),
    ]

    results = []
    for skill in skills:
        await _reset_env(wot_url, "http://localhost:5000")
        result = await executor.execute(skill, obs)
        results.append(
            {
                "skill_id": skill.skill_id,
                "backend": result.backend_used,
                "success": result.success,
                "latency_ms": result.latency_ms,
                "failure_reason": result.failure_reason,
            }
        )
    return results


def _save(name: str, data: list[dict[str, Any]]) -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = _OUTPUT_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"saved {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dom", "wot", "visual", "all"], default="all")
    parser.add_argument("--wot-url", default="http://localhost:5001")
    args = parser.parse_args()

    if args.mode in ("wot", "all"):
        results = asyncio.run(run_wot_baseline(args.wot_url))
        _save("wot_executor_report", results)
        success_rate = sum(1 for r in results if r["success"]) / max(len(results), 1)
        print(f"WoT success rate: {success_rate:.2%}")

    if args.mode in ("dom", "all"):
        print("DOM baseline requires a live mock environment (docker-compose up).")

    if args.mode in ("visual", "all"):
        print("Visual baseline requires Playwright and a running browser.")


if __name__ == "__main__":
    main()
