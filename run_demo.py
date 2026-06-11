"""Run and inspect the Week-6 smart-room demo.

Default mode is offline and deterministic: it writes the trace artifacts used
for presentation even when Docker/Playwright are not available. With
``--probe-env`` it also checks the live React/node-wot environment endpoints.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from evaluation.integration_eval import write_demo_artifacts


def _get_json(url: str, *, timeout_s: float = 2.0) -> tuple[bool, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - local demo endpoint
            body = response.read().decode("utf-8")
            ctype = response.headers.get("content-type", "")
            return response.status < 500, json.loads(body) if "json" in ctype or body.startswith("{") else body[:120]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)


def probe_environment(web_url: str, wot_url: str, control_url: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["dashboard"] = dict(zip(("ok", "detail"), _get_json(web_url), strict=True))
    for thing in ("thermostat", "lights", "projector"):
        ok, detail = _get_json(f"{wot_url.rstrip('/')}/{thing}")
        checks[f"td_{thing}"] = {"ok": ok, "detail": detail}
    ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
    checks["control_plane"] = {"ok": ok, "detail": detail}
    checks["all_ok"] = all(item["ok"] for item in checks.values() if isinstance(item, dict))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Week-6 smart-room demo runner.")
    parser.add_argument("--probe-env", action="store_true", help="Check live Docker env endpoints as well.")
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--wot-url", default="http://localhost:8080")
    parser.add_argument("--control-url", default="http://localhost:8081")
    parser.add_argument("--output-dir", default="artifacts")
    args = parser.parse_args()

    paths = write_demo_artifacts(Path(args.output_dir))
    summary: dict[str, Any] = {
        "artifacts": {key: str(path) for key, path in paths.items()},
        "offline_demo": "ok",
        "next_step": "Run `docker compose -f env/docker-compose.yml up --build` and open http://localhost:3000.",
    }
    if args.probe_env:
        summary["environment"] = probe_environment(args.web_url, args.wot_url, args.control_url)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
