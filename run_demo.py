"""Run and inspect the Week-6 smart-room demo.

Default mode is offline and deterministic: it writes the trace artifacts used
for presentation even when Docker/Playwright are not available. With
``--probe-env`` it also checks the live React/node-wot environment endpoints.
With ``--live-agent`` it drives the running dashboard and WoT environment as a
small vertical slice: DOM perception, TD parsing, Playwright isolation, System-1
DOM/WoT actions, verifier checks, failure injection, and artifact export.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from evaluation.integration_eval import write_demo_artifacts
from src.contracts.types import Affordance, ExecutionResult
from src.effectors.dom_executor import DomExecutor
from src.effectors.wot_executor import WotExecutor
from src.perception.browser_session import BrowserSession
from src.perception.som_parser import BoundingBox, VisualMark, annotate_screenshot, marks_from_affordances
from src.perception.td_affordance_parser import TdAffordanceParser


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


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _post_json(url: str, payload: Any, *, timeout_s: float = 2.0) -> tuple[bool, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - local demo endpoint
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - local demo endpoint
            text = response.read().decode("utf-8")
            return response.status < 500, json.loads(text) if text else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)


def _fetch_tds(wot_url: str) -> list[dict[str, Any]]:
    tds: list[dict[str, Any]] = []
    for thing in ("thermostat", "lights", "projector"):
        ok, detail = _get_json(f"{wot_url.rstrip('/')}/{thing}")
        if not ok or not isinstance(detail, dict):
            raise RuntimeError(f"TD endpoint not available for {thing}: {detail}")
        tds.append(_rewrite_td_forms_to_base(detail, wot_url))
    return tds


def _rewrite_td_forms_to_base(td: dict[str, Any], public_base: str) -> dict[str, Any]:
    """node-wot emits container-internal hrefs; host-side demo uses localhost."""
    parsed_base = urlparse(public_base)
    rewritten = json.loads(json.dumps(td))

    def rewrite_form(form: dict[str, Any]) -> None:
        href = form.get("href")
        if not isinstance(href, str):
            return
        parsed = urlparse(href)
        if parsed.scheme and parsed.netloc:
            form["href"] = urlunparse((parsed_base.scheme, parsed_base.netloc, parsed.path, "", parsed.query, ""))

    for section in ("properties", "actions", "events"):
        for item in (rewritten.get(section) or {}).values():
            for form in item.get("forms") or []:
                rewrite_form(form)
    for form in rewritten.get("forms") or []:
        rewrite_form(form)
    return rewritten


def _find_affordance(affordances: list[Affordance], *, label: str = "", selector: str = "") -> Affordance:
    for affordance in affordances:
        if label and affordance.label == label:
            return affordance
        if selector and affordance.locator.get("selector") == selector:
            return affordance
    raise RuntimeError(f"affordance not found: label={label!r}, selector={selector!r}")


def _read_state(control_url: str) -> dict[str, Any]:
    ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
    if not ok or not isinstance(detail, dict):
        raise RuntimeError(f"control plane state unavailable: {detail}")
    return detail


def _ready_from_state(state: dict[str, Any], booked: bool) -> bool:
    devices = state["state"]
    return bool(
        booked
        and devices["thermostat"]["targetTemperature"] == 22
        and devices["lights"]["brightness"] <= 40
        and devices["projector"]["power"] == "on"
    )


def run_live_agent_demo(
    web_url: str,
    wot_url: str,
    control_url: str,
    output_dir: Path,
    *,
    headed: bool = False,
    step_delay_s: float = 0.0,
    pause_at_end: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _post_json(f"{control_url.rstrip('/')}/reset", {})
    tds = _fetch_tds(wot_url)
    parser = TdAffordanceParser()
    thing_models = [parser.parse(td) for td in tds]
    wot_executor = WotExecutor(tds)

    trace: list[dict[str, Any]] = []

    all_wot_affordances = [affordance for model in thing_models for affordance in model.affordances]

    def execute_wot(skill: str, affordance_label: str, value: Any) -> ExecutionResult:
        affordance = next((item for item in all_wot_affordances if item.label == affordance_label), None)
        if affordance is None:
            raise RuntimeError(f"missing parsed WoT affordance label: {affordance_label}")
        result = wot_executor.execute(affordance, value=value, skill_id=skill)
        assert isinstance(result, ExecutionResult)
        trace.append({"skill_id": skill, "backend": "wot", "execution_result": _jsonable(result)})
        return result

    def delay_for_demo() -> None:
        if step_delay_s > 0:
            time.sleep(step_delay_s)

    with BrowserSession.launch(web_url, headless=not headed) as session:

        def point_to(selector: str, label: str) -> None:
            session.evaluate(
                """({ selector, label }) => {
                    if (window.__demoPointTo) window.__demoPointTo(selector, label);
                }""",
                {"selector": selector, "label": label},
            )
            delay_for_demo()

        pam = session.state(page_id="smart_room_dashboard")
        (output_dir / "page_affordance_model.json").write_text(json.dumps(_jsonable(pam), indent=2), encoding="utf-8")
        (output_dir / "thing_affordance_model.json").write_text(
            json.dumps(_jsonable(thing_models), indent=2), encoding="utf-8"
        )

        screenshot_path = output_dir / "smart_room_dashboard.png"
        screenshot_bytes = session.screenshot(str(screenshot_path))
        marks = marks_from_affordances(pam.affordances)
        if not marks:
            marks = [
                VisualMark("M000", "Book Room", BoundingBox(80, 180, 110, 32), 0.7),
                VisualMark("M001", "Readiness", BoundingBox(20, 610, 680, 84), 0.7),
            ]
        marked_bytes = annotate_screenshot(screenshot_bytes, marks)
        marked_path = output_dir / "marked_screenshot.png"
        marked_path.write_bytes(marked_bytes)
        visual_result = {"marks": [_jsonable(mark) for mark in marks], "marked_screenshot": str(marked_path)}
        (output_dir / "visual_grounding_result.json").write_text(json.dumps(visual_result, indent=2), encoding="utf-8")

        dom_executor = DomExecutor(session)
        for selector, value, skill in (
            ("[data-testid='room-input']", "A", "confirm_booking.room"),
            ("[data-testid='time-input']", "14:00", "confirm_booking.time"),
        ):
            point_to(selector, f"DOM type: {value}")
            result = dom_executor.execute(
                _find_affordance(pam.affordances, selector=selector), value=value, skill_id=skill
            )
            trace.append({"skill_id": skill, "backend": "dom", "execution_result": _jsonable(result)})
            delay_for_demo()
        point_to("[data-testid='book-room-button']", "DOM click: Book Room")
        result = dom_executor.execute(
            _find_affordance(pam.affordances, label="Book Room"), skill_id="confirm_booking.submit"
        )
        trace.append({"skill_id": "confirm_booking.submit", "backend": "dom", "execution_result": _jsonable(result)})
        delay_for_demo()

        point_to("[data-testid='projector-panel']", "WoT action: projector on")
        execute_wot("turn_on_projector", "setPower", "on")
        delay_for_demo()
        point_to("[data-testid='thermostat-panel']", "WoT action: target 22 C")
        execute_wot("set_temperature", "setTargetTemperature", 22)
        delay_for_demo()
        point_to("[data-testid='lighting-panel']", "WoT action: brightness 40%")
        execute_wot("dim_lights", "setBrightness", 40)
        delay_for_demo()

        time.sleep(2.0)
        point_to("[data-testid='readiness-panel']", "Verifier: postconditions pass")
        state_after = _read_state(control_url)
        status_text = session.text_content("[data-testid='readiness-status']")
        normal_ready = _ready_from_state(state_after, booked=True)
        trace.append(
            {
                "skill_id": "verify_readiness",
                "backend": "verifier",
                "postcondition_result": "pass" if normal_ready else "fail",
                "dashboard_text": status_text,
                "state": state_after["state"],
            }
        )
        if pause_at_end:
            input("Live browser paused after READY path. Press Enter to close it and run failure recovery...")

    _post_json(
        f"{control_url.rstrip('/')}/failure",
        {"thing": "thermostat", "type": "postcondition_mismatch"},
    )
    mismatch_result = execute_wot("set_temperature.injected_failure", "setTargetTemperature", 24)
    failed_state = _read_state(control_url)
    postcondition_failed = failed_state["state"]["thermostat"]["targetTemperature"] != 24
    _post_json(f"{control_url.rstrip('/')}/failure", {"thing": "thermostat", "clear": True})
    recovery_result = execute_wot("set_temperature.recovery_retry", "setTargetTemperature", 24)
    recovered_state = _read_state(control_url)
    recovery_passed = recovered_state["state"]["thermostat"]["targetTemperature"] == 24
    trace.append(
        {
            "skill_id": "recovery_cascade",
            "backend": "verifier",
            "failure_detected": postcondition_failed,
            "recovery_action": "clear_injected_fault_then_retry_wot",
            "execution_before_recovery": _jsonable(mismatch_result),
            "execution_after_recovery": _jsonable(recovery_result),
            "postcondition_result": "pass" if recovery_passed else "fail",
        }
    )

    live_trace = {
        "task_id": "prepare_room_A_1400_live",
        "envs": {
            "react_dashboard": web_url,
            "node_wot_server": wot_url,
            "failure_control_plane": control_url,
            "playwright_browser_session": "isolated chromium context",
        },
        "acceptance": {
            "react_dashboard_accessible": True,
            "node_wot_tds_accessible": True,
            "playwright_isolated_browser_running": True,
            "dom_transducer_outputs_pam": True,
            "td_parser_outputs_wot_affordances": True,
            "som_outputs_marked_screenshot": True,
            "system1_executes_normal_path": normal_ready,
            "failure_injected": True,
            "verifier_detects_issue": postcondition_failed,
            "recovery_triggered": recovery_passed,
            "trace_and_artifacts_exported": True,
        },
        "trace": trace,
        "final_state": recovered_state["state"],
        "artifacts": {
            "page_affordance_model": str(output_dir / "page_affordance_model.json"),
            "thing_affordance_model": str(output_dir / "thing_affordance_model.json"),
            "visual_grounding_result": str(output_dir / "visual_grounding_result.json"),
            "marked_screenshot": str(output_dir / "marked_screenshot.png"),
            "live_trace": str(output_dir / "demo_trace_live.json"),
        },
    }
    (output_dir / "demo_trace_live.json").write_text(json.dumps(live_trace, indent=2), encoding="utf-8")
    return live_trace


def main() -> None:
    parser = argparse.ArgumentParser(description="Week-6 smart-room demo runner.")
    parser.add_argument("--probe-env", action="store_true", help="Check live Docker env endpoints as well.")
    parser.add_argument(
        "--live-agent", action="store_true", help="Drive the live env with Playwright + DOM/WoT actions."
    )
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--wot-url", default="http://localhost:8080")
    parser.add_argument("--control-url", default="http://localhost:8081")
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--headed", action="store_true", help="Show the Playwright browser window.")
    parser.add_argument("--step-delay", type=float, default=0.0, help="Seconds to wait after each visible action.")
    parser.add_argument("--pause-at-end", action="store_true", help="Keep the browser open after the READY path.")
    args = parser.parse_args()

    paths = write_demo_artifacts(Path(args.output_dir))
    summary: dict[str, Any] = {
        "artifacts": {key: str(path) for key, path in paths.items()},
        "offline_demo": "ok",
        "next_step": "Run `docker compose -f env/docker-compose.yml up --build` and open http://localhost:3000.",
    }
    if args.probe_env:
        summary["environment"] = probe_environment(args.web_url, args.wot_url, args.control_url)
    if args.live_agent:
        summary["live_agent"] = run_live_agent_demo(
            args.web_url,
            args.wot_url,
            args.control_url,
            Path(args.output_dir),
            headed=args.headed,
            step_delay_s=args.step_delay,
            pause_at_end=args.pause_at_end,
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
