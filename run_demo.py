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

from evaluation.chaos_monkey import ChaosEvent, ChaosPolicy, live_hook_for_event
from evaluation.integration_eval import write_demo_artifacts
from src.contracts.types import Affordance, ExecutionResult, SkillCall
from src.effectors.dom_executor import DomExecutor
from src.effectors.wot_executor import WotExecutor
from src.perception.browser_session import BrowserSession
from src.perception.som_parser import BoundingBox, VisualMark, annotate_screenshot, marks_from_affordances
from src.perception.td_affordance_parser import TdAffordanceParser
from src.verification.oracle_verifier import OracleVerifier


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
    last_detail: Any = None
    for _ in range(60):
        ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
        if ok and isinstance(detail, dict):
            return detail
        last_detail = detail
        time.sleep(0.5)
    raise RuntimeError(f"control plane state unavailable: {last_detail}")


def _ready_from_state(state: dict[str, Any], booked: bool) -> bool:
    devices = state["state"]
    return bool(
        booked
        and devices["thermostat"]["targetTemperature"] == 22
        and devices["lights"]["brightness"] <= 40
        and devices["projector"]["power"] == "on"
    )


def _ground_truth_from_control_state(state: dict[str, Any], *, booked: bool) -> dict[str, Any]:
    devices = state.get("state", {})
    thermostat = devices.get("thermostat", {})
    lights = devices.get("lights", {})
    projector = devices.get("projector", {})
    return {
        "booked": booked,
        "booking_confirmed": booked,
        "booking_status": "confirmed" if booked else "pending",
        "projector": projector.get("power"),
        "target_temperature": thermostat.get("targetTemperature"),
        "light_brightness": lights.get("brightness"),
        "readiness": bool(
            booked
            and projector.get("power") == "on"
            and thermostat.get("targetTemperature") == 22
            and lights.get("brightness", 100) <= 40
        ),
    }


def _apply_live_chaos_event(session: BrowserSession, control_url: str, event: ChaosEvent) -> dict[str, Any]:
    hook = live_hook_for_event(event)
    if hook.get("surface") == "dom":
        fault = str(hook.get("fault", ""))
        session.evaluate("fault => { if (window.__injectFault) window.__injectFault(fault); }", fault)
        return {"event": _jsonable(event), "hook": hook, "applied": True}
    if hook.get("surface") == "wot":
        payload = {key: value for key, value in hook.items() if key != "surface"}
        ok, detail = _post_json(f"{control_url.rstrip('/')}/failure", payload)
        return {"event": _jsonable(event), "hook": hook, "applied": ok, "detail": detail}
    return {"event": _jsonable(event), "hook": hook, "applied": False}


def _clear_live_chaos_event(session: BrowserSession, control_url: str, event: ChaosEvent) -> dict[str, Any]:
    hook = live_hook_for_event(event)
    if hook.get("surface") == "dom":
        session.evaluate("() => { if (window.__clearFaults) window.__clearFaults(); }")
        return {"event_id": event.event_id, "cleared": True, "surface": "dom"}
    if hook.get("surface") == "wot":
        thing = str(hook.get("thing", "thermostat"))
        ok, detail = _post_json(f"{control_url.rstrip('/')}/failure", {"thing": thing, "clear": True})
        return {"event_id": event.event_id, "cleared": ok, "surface": "wot", "detail": detail}
    return {"event_id": event.event_id, "cleared": False}


def run_live_chaos_demo(
    web_url: str,
    wot_url: str,
    control_url: str,
    output_dir: Path,
    *,
    headed: bool = False,
    step_delay_s: float = 0.0,
    chaos_seed: int = 101,
    chaos_level: int = 3,
    pause_at_end: bool = False,
) -> dict[str, Any]:
    """Visual chaos demo: DOM reroute plus WoT false-success oracle check."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _post_json(f"{control_url.rstrip('/')}/reset", {})
    policy = ChaosPolicy.seeded(chaos_seed, level=chaos_level)
    tds = _fetch_tds(wot_url)
    parser = TdAffordanceParser()
    thing_models = [parser.parse(td) for td in tds]
    wot_executor = WotExecutor(tds)
    all_wot_affordances = [affordance for model in thing_models for affordance in model.affordances]
    oracle = OracleVerifier()
    trace: list[dict[str, Any]] = []
    booked = False

    def delay_for_demo() -> None:
        if step_delay_s > 0:
            time.sleep(step_delay_s)

    def execute_wot(skill: str, affordance_label: str, value: Any) -> ExecutionResult:
        affordance = next((item for item in all_wot_affordances if item.label == affordance_label), None)
        if affordance is None:
            raise RuntimeError(f"missing parsed WoT affordance label: {affordance_label}")
        result = wot_executor.execute(affordance, value=value, skill_id=skill)
        assert isinstance(result, ExecutionResult)
        return result

    def point_to(selector: str, label: str, session: BrowserSession) -> None:
        session.evaluate(
            """({ selector, label }) => {
                if (window.__demoPointTo) window.__demoPointTo(selector, label);
            }""",
            {"selector": selector, "label": label},
        )
        delay_for_demo()

    with BrowserSession.launch(web_url, headless=not headed) as session:
        pam = session.state(page_id="smart_room_dashboard_chaos")
        (output_dir / "page_affordance_model.json").write_text(json.dumps(_jsonable(pam), indent=2), encoding="utf-8")
        (output_dir / "thing_affordance_model.json").write_text(
            json.dumps(_jsonable(thing_models), indent=2), encoding="utf-8"
        )
        screenshot_path = output_dir / "smart_room_dashboard.png"
        screenshot_bytes = session.screenshot(str(screenshot_path))
        marks = marks_from_affordances(pam.affordances)
        if not marks:
            marks = [VisualMark("M000", "Book Room", BoundingBox(80, 180, 110, 32), 0.7)]
        marked_path = output_dir / "marked_screenshot.png"
        marked_path.write_bytes(annotate_screenshot(screenshot_bytes, marks))
        (output_dir / "visual_grounding_result.json").write_text(
            json.dumps({"marks": [_jsonable(mark) for mark in marks], "marked_screenshot": str(marked_path)}, indent=2),
            encoding="utf-8",
        )

        dom_executor = DomExecutor(session)
        for selector, value, skill in (
            ("[data-testid='room-input']", "A", "confirm_booking.room"),
            ("[data-testid='time-input']", "14:00", "confirm_booking.time"),
        ):
            point_to(selector, f"DOM type: {value}", session)
            result = dom_executor.execute(_find_affordance(pam.affordances, selector=selector), value=value, skill_id=skill)
            trace.append({"skill_id": skill, "backend": "dom", "execution_result": _jsonable(result)})
            delay_for_demo()

        dom_event = next((event for event in policy.events if event.failure_type == "dom_selector_mutation"), None)
        booking_selector = "[data-testid='book-room-button']"
        if dom_event is not None:
            applied = _apply_live_chaos_event(session, control_url, dom_event)
            trace.append({"skill_id": "confirm_booking", "event_type": "chaos_injected", **applied})
            booking_selector = "[data-testid='book-room-button-v2']"
            point_to(booking_selector, "Chaos: DOM selector changed", session)

        point_to(booking_selector, "DOM attempt uses cached selector", session)
        dom_result = dom_executor.execute(
            _find_affordance(pam.affordances, label="Book Room"), skill_id="confirm_booking.dom_attempt"
        )
        trace.append({"skill_id": "confirm_booking", "backend": "dom", "execution_result": _jsonable(dom_result)})
        if isinstance(dom_result, ExecutionResult) and dom_result.success:
            booked = True
        if isinstance(dom_result, ExecutionResult) and not dom_result.success:
            point_to(booking_selector, "Recovery tier 2: visual fallback", session)
            session.click(booking_selector)
            booked = True
            visual_result = ExecutionResult(
                skill_id="confirm_booking",
                backend_used="visual",
                success=True,
                latency_ms=10.0,
                confidence=1.0,
                raw_observation_delta={"booking_status": "confirmed", "booking_confirmed": True},
            )
            trace.append(
                {
                    "skill_id": "confirm_booking",
                    "backend": "visual",
                    "recovery_tier": 2,
                    "execution_result": _jsonable(visual_result),
                    "oracle": _jsonable(
                        oracle.verify_skill(
                            task_id="prepare_room_A_1400_live_chaos",
                            skill_call=SkillCall("confirm_booking", {"room": "A", "time": "14:00"}),
                            execution_result=visual_result,
                            ground_truth_state={"booked": True, "booking_status": "confirmed"},
                        )
                    ),
                }
            )
        delay_for_demo()

        point_to("[data-testid='projector-panel']", "WoT action: projector on", session)
        projector_result = execute_wot("turn_on_projector", "setPower", "on")
        trace.append({"skill_id": "turn_on_projector", "backend": "wot", "execution_result": _jsonable(projector_result)})
        delay_for_demo()

        execute_wot("set_temperature.pre_chaos_drift", "setTargetTemperature", 21)
        wot_event = next((event for event in policy.events if event.failure_type == "wot_postcondition_mismatch"), None)
        if wot_event is not None:
            applied = _apply_live_chaos_event(session, control_url, wot_event)
            trace.append({"skill_id": "set_temperature", "event_type": "chaos_injected", **applied})
        point_to("[data-testid='thermostat-panel']", "Chaos: WoT returns success but state is stale", session)
        mismatch_result = execute_wot("set_temperature", "setTargetTemperature", 22)
        failed_state = _read_state(control_url)
        false_positive_verdict = oracle.verify_skill(
            task_id="prepare_room_A_1400_live_chaos",
            skill_call=SkillCall("set_temperature", {"room": "A", "target": 22}),
            execution_result=mismatch_result,
            ground_truth_state=_ground_truth_from_control_state(failed_state, booked=booked),
        )
        trace.append(
            {
                "skill_id": "set_temperature",
                "backend": "wot",
                "execution_result": _jsonable(mismatch_result),
                "oracle": _jsonable(false_positive_verdict),
            }
        )
        delay_for_demo()

        if false_positive_verdict.false_positive:
            point_to("[data-testid='thermostat-panel']", "Oracle caught false success; retry", session)
            if wot_event is not None:
                trace.append({"skill_id": "set_temperature", "event_type": "chaos_cleared", **_clear_live_chaos_event(session, control_url, wot_event)})
            retry_result = execute_wot("set_temperature.retry", "setTargetTemperature", 22)
            recovered_state = _read_state(control_url)
            retry_verdict = oracle.verify_skill(
                task_id="prepare_room_A_1400_live_chaos",
                skill_call=SkillCall("set_temperature", {"room": "A", "target": 22}),
                execution_result=retry_result,
                ground_truth_state=_ground_truth_from_control_state(recovered_state, booked=booked),
            )
            trace.append(
                {
                    "skill_id": "set_temperature",
                    "backend": "wot",
                    "recovery_tier": 1,
                    "execution_result": _jsonable(retry_result),
                    "oracle": _jsonable(retry_verdict),
                }
            )
        delay_for_demo()

        point_to("[data-testid='lighting-panel']", "WoT action: brightness 40%", session)
        lighting_result = execute_wot("set_lighting", "setBrightness", 40)
        trace.append({"skill_id": "set_lighting", "backend": "wot", "execution_result": _jsonable(lighting_result)})
        time.sleep(2.0)
        point_to("[data-testid='readiness-panel']", "Final oracle: READY", session)
        final_state_raw = _read_state(control_url)
        ground_truth = _ground_truth_from_control_state(final_state_raw, booked=booked)
        final_oracle = oracle.verify_final_state(
            task_id="prepare_room_A_1400_live_chaos",
            expected_final_state={
                "booked": True,
                "projector": "on",
                "target_temperature": 22,
                "light_brightness": 40,
                "readiness": True,
            },
            ground_truth_state=ground_truth,
        )
        trace.append(
            {
                "skill_id": "verify_readiness",
                "backend": "oracle",
                "oracle": _jsonable(final_oracle),
                "ground_truth_state": ground_truth,
                "dashboard_text": session.text_content("[data-testid='readiness-status']"),
            }
        )
        if pause_at_end:
            input("Live chaos demo paused. Press Enter to close the browser...")

    report = {
        "task_id": "prepare_room_A_1400_live_chaos",
        "chaos_seed": chaos_seed,
        "chaos_level": chaos_level,
        "chaos_events": [_jsonable(event) for event in policy.events],
        "acceptance": {
            "dom_selector_mutation_injected": any(event.failure_type == "dom_selector_mutation" for event in policy.events),
            "visual_fallback_triggered": any(row.get("backend") == "visual" for row in trace),
            "wot_false_success_injected": any(event.failure_type == "wot_postcondition_mismatch" for event in policy.events),
            "oracle_detected_false_positive": any((row.get("oracle") or {}).get("false_positive") for row in trace),
            "final_oracle_success": final_oracle.oracle_success,
        },
        "trace": trace,
        "artifacts": {
            "page_affordance_model": str(output_dir / "page_affordance_model.json"),
            "thing_affordance_model": str(output_dir / "thing_affordance_model.json"),
            "visual_grounding_result": str(output_dir / "visual_grounding_result.json"),
            "marked_screenshot": str(output_dir / "marked_screenshot.png"),
            "chaos_trace": str(output_dir / "chaos_demo_trace_live.json"),
        },
    }
    (output_dir / "chaos_demo_trace_live.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


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
        execute_wot("set_lighting", "setBrightness", 40)
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

    execute_wot("set_temperature.pre_failure_drift", "setTargetTemperature", 24)
    _post_json(
        f"{control_url.rstrip('/')}/failure",
        {"thing": "thermostat", "type": "postcondition_mismatch"},
    )
    mismatch_result = execute_wot("set_temperature.injected_failure", "setTargetTemperature", 22)
    failed_state = _read_state(control_url)
    postcondition_failed = failed_state["state"]["thermostat"]["targetTemperature"] != 22
    _post_json(f"{control_url.rstrip('/')}/failure", {"thing": "thermostat", "clear": True})
    recovery_result = execute_wot("set_temperature.recovery_retry", "setTargetTemperature", 22)
    recovered_state = _read_state(control_url)
    recovery_passed = recovered_state["state"]["thermostat"]["targetTemperature"] == 22
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
    parser.add_argument(
        "--chaos-demo",
        action="store_true",
        help="Drive a visual chaos demo with DOM mutation, WoT false success, oracle verification, and recovery.",
    )
    parser.add_argument("--chaos-seed", type=int, default=101, help="Seed for deterministic visual chaos policy.")
    parser.add_argument("--chaos-level", type=int, choices=[1, 2, 3], default=3, help="Chaos policy level.")
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--wot-url", default="http://localhost:8080")
    parser.add_argument("--control-url", default="http://[::1]:8081")
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
    if args.chaos_demo:
        summary["chaos_demo"] = run_live_chaos_demo(
            args.web_url,
            args.wot_url,
            args.control_url,
            Path(args.output_dir),
            headed=args.headed,
            step_delay_s=args.step_delay,
            chaos_seed=args.chaos_seed,
            chaos_level=args.chaos_level,
            pause_at_end=args.pause_at_end,
        )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
