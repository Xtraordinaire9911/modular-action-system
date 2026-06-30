"""Fixture-driven live runtime evaluation for the smart-room demo.

This module keeps the existing offline traces intact and provides a separate
live path that consumes Member A's fixtures, drives the real executors through
``ContinuousInteractionManager``, and records the achieved recovery tier per
step.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from threading import Thread
from urllib.parse import urlparse, urlunparse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.contracts.types import Observation, SkillCall
from src.effectors.dom_executor import DomExecutor
from src.effectors.visual_executor import VisualExecutor
from src.effectors.wot_executor import WotExecutor
from src.perception.som_parser import BoundingBox, VisualMark, annotate_screenshot, marks_from_affordances
from src.perception.dom_transducer import DomTransducer
from src.perception.td_affordance_parser import TdAffordanceParser
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager, RuntimeStepResult
from src.runtime.state_machine import RuntimeState
from src.skill_library import FailureProfile, TaskFixture, expected_skill_calls, get_task_fixture, load_failure_profiles
from src.skill_library.library import load_skill_library
from playwright.async_api import async_playwright


def _get_json(url: str, *, timeout_s: float = 2.0) -> tuple[bool, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - local demo endpoint
            body = response.read().decode("utf-8")
            ctype = response.headers.get("content-type", "")
            return response.status < 500, json.loads(body) if "json" in ctype or body.startswith("{") else body
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)


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


def _rewrite_td_forms_to_base(td: dict[str, Any], public_base: str) -> dict[str, Any]:
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


def _fetch_tds(wot_url: str) -> list[dict[str, Any]]:
    tds: list[dict[str, Any]] = []
    for thing in ("thermostat", "lights", "projector"):
        ok, detail = _get_json(f"{wot_url.rstrip('/')}/{thing}")
        if not ok or not isinstance(detail, dict):
            raise RuntimeError(f"TD endpoint not available for {thing}: {detail}")
        tds.append(_rewrite_td_forms_to_base(detail, wot_url))
    return tds


async def _wait_for_dashboard_hooks(page: Any, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = await page.evaluate("() => Boolean(window.__injectFault && window.__clearFaults && window.__demoPointTo)")
        if ready:
            return
        time.sleep(0.1)
    raise RuntimeError("dashboard hooks were not initialized in time")


def _reset_web_dashboard(web_url: str, task_fixture: TaskFixture) -> None:
    override: dict[str, Any] = {"bookings": {}}
    room = str(task_fixture.initial_state.get("room", "A")).strip().upper()
    booked = bool(task_fixture.initial_state.get("booked", False))
    if booked:
        time_slot = task_fixture.expected_skill_sequence and expected_skill_calls(task_fixture.task_id)[0].params.get("time")
        override["bookings"][room] = {"booked": True, "time": time_slot or "14:00"}
    _post_json(f"{web_url.rstrip('/')}/api/reset", override)


def _reset_wot_server(wot_url: str, task_fixture: TaskFixture, failure_profile: FailureProfile | None) -> None:
    override: dict[str, Any] = {}
    initial_state = task_fixture.initial_state
    thermostat_target = initial_state.get("target_temperature_wot", initial_state.get("target_temperature"))
    if thermostat_target is not None:
        thermostat_state: dict[str, Any] = {"targetTemperature": thermostat_target}
        current_temperature = initial_state.get("current_temperature")
        if current_temperature is not None:
            thermostat_state["currentTemperature"] = current_temperature
        override["thermostat"] = thermostat_state
    if "light_brightness" in initial_state:
        override["lights"] = {"brightness": initial_state["light_brightness"]}
    if initial_state.get("projector"):
        override["projector"] = {"power": initial_state["projector"]}
    if failure_profile and failure_profile.failure_id == "sensory_contradiction":
        override.setdefault("thermostat", {})["targetTemperature"] = int(failure_profile.extra.get("wot_value", 24))
    _post_json(f"{wot_url.rstrip('/')}/api/reset", override)


def _reset_control_plane(control_url: str) -> None:
    _post_json(f"{control_url.rstrip('/')}/reset", {})


def _inject_wot_fault(control_url: str, failure_type: str, thing: str = "thermostat") -> None:
    _post_json(f"{control_url.rstrip('/')}/failure", {"thing": thing, "type": failure_type})


def _clear_wot_fault(control_url: str, thing: str = "thermostat") -> None:
    _post_json(f"{control_url.rstrip('/')}/failure", {"thing": thing, "clear": True})


async def _inject_dashboard_fault(page: Any, fault: str | None) -> None:
    if not fault:
        return
    await page.evaluate("fault => { if (window.__injectFault) window.__injectFault(fault); }", fault)
    time.sleep(0.2)


async def _clear_dashboard_faults(page: Any) -> None:
    await page.evaluate("() => { if (window.__clearFaults) window.__clearFaults(); }")


def _canonicalize_wot_state(state: dict[str, Any]) -> dict[str, Any]:
    devices = state.get("state") if isinstance(state.get("state"), dict) else state
    result: dict[str, Any] = {
        "booking_service_available": True,
        "projector_service_available": True,
        "thermostat_service_available": True,
        "lighting_service_available": True,
    }
    if isinstance(devices, dict):
        thermostat = devices.get("thermostat")
        if isinstance(thermostat, dict):
            result["thermostat"] = {
                "target_temperature": thermostat.get("targetTemperature"),
                "current_temperature": thermostat.get("currentTemperature"),
            }
        lights = devices.get("lights")
        if isinstance(lights, dict):
            result["lighting"] = {"brightness": lights.get("brightness")}
        projector = devices.get("projector")
        if isinstance(projector, dict):
            result["projector"] = {"power": projector.get("power")}
        readiness = devices.get("readiness")
        if isinstance(readiness, dict):
            result["readiness"] = {"ready": readiness.get("ready")}
        if "readiness" not in result:
            projector_power = (result.get("projector") or {}).get("power")
            target_temperature = (result.get("thermostat") or {}).get("target_temperature")
            brightness = (result.get("lighting") or {}).get("brightness")
            result["readiness"] = {
                "ready": bool(
                    projector_power == "on"
                    and target_temperature is not None
                    and float(target_temperature) == 22.0
                    and brightness is not None
                    and float(brightness) <= 40.0
                )
            }
    return result


def _build_observation(wot_state: dict[str, Any], *, booking_status: str) -> Observation:
    device_states = _canonicalize_wot_state(wot_state)
    device_states["booking_status"] = booking_status
    if booking_status:
        device_states["booking_confirmed"] = booking_status == "confirmed"
    return Observation(device_states=device_states, accessibility_tree={"page_state": {"booking_status": booking_status}})


async def _booking_status(page: Any) -> str:
    text = await page.text_content("[data-testid='booking-status']") or ""
    return "confirmed" if "booked:" in text.lower() else "pending"


async def _final_state(page: Any, wot_state: dict[str, Any]) -> dict[str, Any]:
    devices = _canonicalize_wot_state(wot_state)
    booking = await _booking_status(page) == "confirmed"
    return {
        "booked": booking,
        "projector": (devices.get("projector") or {}).get("power"),
        "target_temperature": (devices.get("thermostat") or {}).get("target_temperature"),
        "light_brightness": (devices.get("lighting") or {}).get("brightness"),
        "readiness": (devices.get("readiness") or {}).get("ready"),
    }


def _failure_profile_for_task(task_fixture: TaskFixture, override: str | None = None) -> FailureProfile | None:
    target = override or task_fixture.allowed_failure_profile
    if target is None:
        return None
    for profile in load_failure_profiles():
        if profile.failure_id == target:
            return profile
    raise RuntimeError(f"unknown failure profile: {target}")


async def _run_skill_with_recovery(
    manager: ContinuousInteractionManager,
    skill_call: SkillCall,
    observation_factory: Callable[[], Awaitable[Observation]],
    *,
    clear_dashboard_faults: Callable[[], Awaitable[None]] | None = None,
    clear_wot_fault: Callable[[], None] | None = None,
    reroute_backend: str | None = None,
) -> dict[str, Any]:
    attempts = 0
    attempt_log: list[dict[str, Any]] = []
    current_call = skill_call
    highest_tier = 0

    while attempts < 4:
        attempts += 1
        observation = await observation_factory()
        result: RuntimeStepResult = await manager.run_skill(current_call, observation)
        attempt_log.append(
            {
                "attempt": attempts,
                "state": result.state.value,
                "selected_backend": result.selected_backend,
                "recovery_tier": result.recovery_tier,
                "reason": result.reason,
                "execution_result": None if result.execution_result is None else asdict(result.execution_result),
            }
        )
        if result.state == RuntimeState.COMPLETED:
            return {
                "skill_id": skill_call.skill_id,
                "success": True,
                "attempts": attempts,
                "selected_backend": result.selected_backend,
                "recovery_tier": highest_tier,
                "reason": result.reason,
                "attempt_log": attempt_log,
                "execution_result": None if result.execution_result is None else asdict(result.execution_result),
            }

        tier = int(result.recovery_tier or 4)
        highest_tier = max(highest_tier, tier)
        if tier == 1:
            if clear_wot_fault is not None:
                clear_wot_fault()
            if clear_dashboard_faults is not None:
                await clear_dashboard_faults()
            continue
        if tier == 2 and reroute_backend:
            current_call = SkillCall(
                skill_id=skill_call.skill_id,
                params=dict(skill_call.params),
                preferred_backends=[reroute_backend],
            )
            continue

        return {
            "skill_id": skill_call.skill_id,
            "success": False,
            "attempts": attempts,
            "selected_backend": result.selected_backend,
            "recovery_tier": highest_tier,
            "reason": result.reason,
            "attempt_log": attempt_log,
            "execution_result": None if result.execution_result is None else asdict(result.execution_result),
        }

    return {
        "skill_id": skill_call.skill_id,
        "success": False,
        "attempts": attempts,
        "selected_backend": current_call.preferred_backends[0] if current_call.preferred_backends else "",
        "recovery_tier": highest_tier or 4,
        "reason": "recovery loop exhausted",
        "attempt_log": attempt_log,
        "execution_result": None,
    }


@dataclass
class LiveEpisodeResult:
    task_id: str
    user_goal: str
    expected_skill_sequence: list[str]
    expected_recovery_tier: int
    achieved_recovery_tier: int
    recovery_tier_match: bool
    task_success: bool
    steps: list[dict[str, Any]]
    final_state: dict[str, Any]
    expected_final_state: dict[str, Any]
    artifacts: dict[str, str] = field(default_factory=dict)


async def _run_fixture_driven_live_episode(
    *,
    task_id: str = "prepare_room_A_1400",
    web_url: str = "http://localhost:3000",
    wot_url: str = "http://localhost:8080",
    control_url: str = "http://[::1]:8081",
    headed: bool = False,
    step_delay_s: float = 0.0,
    failure_profile_id: str | None = None,
    output_dir: str | Path | None = None,
) -> LiveEpisodeResult:
    task_fixture = get_task_fixture(task_id)
    expected_steps = expected_skill_calls(task_id)
    failure_profile = _failure_profile_for_task(task_fixture, failure_profile_id)
    expected_recovery_tier = int(failure_profile.expected_recovery_tier) if failure_profile else 0

    _reset_control_plane(control_url)
    _reset_web_dashboard(web_url, task_fixture)
    _reset_wot_server(wot_url, task_fixture, failure_profile)
    if failure_profile is not None and failure_profile.failure_id in {"sensory_contradiction", "wot_postcondition_mismatch"}:
        _inject_wot_fault(control_url, "postcondition_mismatch")

    dashboard_url = web_url
    browser_fault = None
    if failure_profile is not None and failure_profile.injection.get("surface") == "dom":
        browser_fault = str(failure_profile.injection.get("value", "")) or None
    elif failure_profile is not None and failure_profile.failure_id == "sensory_contradiction":
        browser_fault = "stale_temperature"

    output_path = Path(output_dir) if output_dir is not None else None
    if output_path is not None:
        output_path.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(8000)
        await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=10_000)

        try:
            await _wait_for_dashboard_hooks(page)
            await _inject_dashboard_fault(page, browser_fault)
            tds = _fetch_tds(wot_url)
            parser = TdAffordanceParser()
            thing_models = [parser.parse(td) for td in tds]
            pam_html = await page.content()
            pam = DomTransducer().transduce(pam_html, page_id="smart_room_dashboard", url=dashboard_url)
            marks = marks_from_affordances(pam.affordances)
            if not marks:
                marks = [
                    VisualMark("M000", "Book Room", BoundingBox(80, 180, 110, 32), 0.7),
                    VisualMark("M001", "Readiness", BoundingBox(20, 610, 680, 84), 0.7),
                ]

            cognitive_map = CognitiveMap(task_id=task_fixture.task_id)
            visual_executor = VisualExecutor(page)
            for step in expected_steps:
                visual_executor.update_marks(step.skill_id, marks)

            manager = ContinuousInteractionManager(
                load_skill_library(),
                {
                    "dom": DomExecutor(page, base_url=web_url),
                    "wot": WotExecutor(tds),
                    "visual": visual_executor,
                },
                cognitive_map,
            )

            steps: list[dict[str, Any]] = []

            async def observation_factory() -> Observation:
                ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
                live_state = detail if ok and isinstance(detail, dict) else {}
                return _build_observation(live_state, booking_status=await _booking_status(page))

            for index, skill_call in enumerate(expected_steps, start=1):
                reroute_backend = "visual" if skill_call.skill_id == "confirm_booking" else None
                step_result = await _run_skill_with_recovery(
                    manager,
                    skill_call,
                    observation_factory,
                    clear_dashboard_faults=(lambda: _clear_dashboard_faults(page))
                    if browser_fault
                    else None,
                    clear_wot_fault=(lambda: _clear_wot_fault(control_url))
                    if failure_profile is not None and failure_profile.failure_id in {"sensory_contradiction", "wot_postcondition_mismatch"}
                    else None,
                    reroute_backend=reroute_backend,
                )
                step_result["step_index"] = index
                steps.append(step_result)
                if step_delay_s > 0:
                    time.sleep(step_delay_s)

            ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
            live_state = detail if ok and isinstance(detail, dict) else {}
            final_state = await _final_state(page, live_state)
            task_success = final_state == task_fixture.expected_final_state
            achieved_recovery_tier = max((int(step.get("recovery_tier", 0) or 0) for step in steps), default=0)

            if output_path is not None:
                live_trace = {
                    "task_id": task_fixture.task_id,
                    "user_goal": task_fixture.user_goal,
                    "expected_skill_sequence": task_fixture.expected_skill_sequence,
                    "expected_recovery_tier": expected_recovery_tier,
                    "achieved_recovery_tier": achieved_recovery_tier,
                    "recovery_tier_match": achieved_recovery_tier == expected_recovery_tier,
                    "task_success": task_success,
                    "steps": steps,
                    "final_state": final_state,
                    "expected_final_state": task_fixture.expected_final_state,
                    "envs": {
                        "react_dashboard": dashboard_url,
                        "node_wot_server": wot_url,
                        "failure_control_plane": control_url,
                        "playwright_browser_session": "isolated chromium context",
                    },
                    "things": [model.thing_id for model in thing_models],
                }
                trace_path = output_path / f"{task_fixture.task_id}_fixture_live_episode.json"
                trace_path.write_text(json.dumps(live_trace, indent=2, sort_keys=True), encoding="utf-8")
                _post_json(f"{control_url.rstrip('/')}/reset", {})
                await _clear_dashboard_faults(page)
                artifacts = {"trace": str(trace_path)}
            else:
                artifacts = {}
        finally:
            await context.close()
            await browser.close()

    return LiveEpisodeResult(
        task_id=task_fixture.task_id,
        user_goal=task_fixture.user_goal,
        expected_skill_sequence=list(task_fixture.expected_skill_sequence),
        expected_recovery_tier=expected_recovery_tier,
        achieved_recovery_tier=achieved_recovery_tier,
        recovery_tier_match=achieved_recovery_tier == expected_recovery_tier,
        task_success=task_success,
        steps=steps,
        final_state=final_state,
        expected_final_state=dict(task_fixture.expected_final_state),
        artifacts=artifacts,
    )


def run_fixture_driven_live_episode(
    *,
    task_id: str = "prepare_room_A_1400",
    web_url: str = "http://localhost:3000",
    wot_url: str = "http://localhost:8080",
    control_url: str = "http://[::1]:8081",
    headed: bool = False,
    step_delay_s: float = 0.0,
    failure_profile_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    result_box: dict[str, LiveEpisodeResult] = {}
    error_box: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result_box["result"] = asyncio.run(
                _run_fixture_driven_live_episode(
                    task_id=task_id,
                    web_url=web_url,
                    wot_url=wot_url,
                    control_url=control_url,
                    headed=headed,
                    step_delay_s=step_delay_s,
                    failure_profile_id=failure_profile_id,
                    output_dir=output_dir,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - surface worker failures to the caller
            error_box["error"] = exc

    worker = Thread(target=_worker, daemon=True)
    worker.start()
    worker.join()
    if "error" in error_box:
        raise error_box["error"]
    return asdict(result_box["result"])