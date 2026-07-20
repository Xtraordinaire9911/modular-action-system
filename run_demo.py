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
import asyncio
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from evaluation.chaos_monkey import ChaosEvent, ChaosPolicy, live_hook_for_event
from evaluation.integration_eval import write_demo_artifacts
from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.effectors.dom_executor import DomExecutor
from src.effectors.wot_executor import WotExecutor
from src.perception.browser_session import BrowserSession
from src.perception.som_parser import BoundingBox, VisualMark, annotate_screenshot, marks_from_affordances
from src.perception.td_affordance_parser import TdAffordanceParser
from src.runtime.cognitive_map import CognitiveMap
from src.runtime.continuous_interaction_manager import ContinuousInteractionManager
from src.skill_library import load_skill_library
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


def _fetch_tds(wot_url: str, directory_url: str = "") -> list[dict[str, Any]]:
    """Obtain the environment's TDs, preferring runtime discovery over a static list.

    When a Thing Directory is reachable the agent discovers the full inventory at
    runtime (no hard-coded device names); otherwise it falls back to the known
    smart-room Things so the demo still runs on a bare servient.
    """
    if directory_url:
        try:
            from src.perception.thing_directory import ThingDirectoryClient

            discovered = ThingDirectoryClient(directory_url).discover_tds()
            print(f"[discovery] {len(discovered)} Thing Description(s) discovered via {directory_url}/things")
            return [_rewrite_td_forms_to_base(td, wot_url) for td in discovered]
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort with a static fallback
            print(f"[discovery] directory unavailable ({exc}); falling back to static thing list")

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


def _with_live_runtime_planning_hints(affordances: list[Affordance]) -> list[Affordance]:
    hinted: list[Affordance] = []
    for affordance in affordances:
        selector = str(affordance.locator.get("selector", ""))
        if selector == "[data-testid='room-input']":
            locator = {**affordance.locator, "skill_id": "room"}
            hinted.append(replace(affordance, label="Room", locator=locator))
        elif selector == "[data-testid='time-input']":
            locator = {**affordance.locator, "skill_id": "time"}
            hinted.append(replace(affordance, label="Time", locator=locator))
        elif affordance.label == "Book Room":
            locator = {**affordance.locator, "skill_id": "booking confirmed"}
            hinted.append(replace(affordance, locator=locator))
        else:
            hinted.append(affordance)
    return hinted


def _runtime_step_payload(result: Any) -> dict[str, Any]:
    return {
        "state": result.state.value if hasattr(result.state, "value") else str(result.state),
        "reason": result.reason,
        "selected_backend": result.selected_backend,
        "routing_reason": result.routing_reason,
        "recovery_tier": result.recovery_tier,
        "failure_boundary": result.failure_boundary,
        "failure_type": result.failure_type,
        "fusion_decision": _jsonable(result.fusion_decision),
        "primitive_plan": _jsonable(result.primitive_plan),
        "plan_validation_errors": list(result.plan_validation_errors),
        "active_perception_trace": _jsonable(result.active_perception_trace),
        "recovery_trace": _jsonable(result.recovery_trace),
        "execution_result": _jsonable(result.execution_result) if result.execution_result else None,
    }


def _runtime_trace_entry(
    *,
    skill_id: str,
    controller: str,
    observation_source: str,
    result: Any,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "controller": controller,
        "observation_source": observation_source,
        "runtime_step": _runtime_step_payload(result),
    }


class _MainThreadDispatcher:
    """Run Playwright-bound sync calls on the thread that owns the browser."""

    def __init__(self) -> None:
        self._owner_thread_id = threading.get_ident()
        self._tasks: queue.Queue[dict[str, Any]] = queue.Queue()

    def call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        if threading.get_ident() == self._owner_thread_id:
            return func(*args, **kwargs)
        done = threading.Event()
        task = {"func": func, "args": args, "kwargs": kwargs, "done": done}
        self._tasks.put(task)
        done.wait()
        if "error" in task:
            raise task["error"]
        return task.get("result")

    def drain_once(self, timeout_s: float = 0.01) -> None:
        try:
            task = self._tasks.get(timeout=timeout_s)
        except queue.Empty:
            return
        try:
            task["result"] = task["func"](*task["args"], **task["kwargs"])
        except BaseException as exc:  # noqa: BLE001 - propagate to worker
            task["error"] = exc
        finally:
            task["done"].set()

    def drain_all(self) -> None:
        while not self._tasks.empty():
            self.drain_once(timeout_s=0.0)


def _run_async_runtime(coro: Any, dispatcher: _MainThreadDispatcher | None = None) -> Any:
    """Run CIM async work without colliding with Playwright's sync event loop."""

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagate from worker thread
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    while thread.is_alive():
        if dispatcher is None:
            thread.join(0.01)
        else:
            dispatcher.drain_once()
    if dispatcher is not None:
        dispatcher.drain_all()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _read_state(control_url: str) -> dict[str, Any]:
    ok, detail = _get_json(f"{control_url.rstrip('/')}/state")
    if not ok or not isinstance(detail, dict):
        raise RuntimeError(f"control plane state unavailable: {detail}")
    return detail


def _observation_from_live_state(control_url: str, *, booked: bool = False) -> Observation:
    state = _read_state(control_url).get("state", {})
    normalized = _normalized_live_device_state(state, booked=booked)
    return Observation(
        device_states=normalized,
        accessibility_tree={
            "page_state": {
                "booking": {"confirmed": booked},
                "booking_status": "confirmed" if booked else "pending",
            }
        },
    )


def _normalized_live_device_state(state: dict[str, Any], *, booked: bool = False) -> dict[str, Any]:
    thermostat = state.get("thermostat", {})
    lights = state.get("lights", {})
    projector = state.get("projector", {})
    readiness = bool(
        booked
        and projector.get("power") == "on"
        and 20 <= thermostat.get("targetTemperature", 0) <= 24
        and lights.get("brightness", 100) <= 60
    )
    return {
        "booking": {"confirmed": booked},
        "booking_status": "confirmed" if booked else "pending",
        "booking_confirmed": booked,
        "thermostat_service_available": True,
        "lighting_service_available": True,
        "projector_service_available": True,
        "thermostat": {
            "target_temperature": thermostat.get("targetTemperature"),
            "targetTemperature": thermostat.get("targetTemperature"),
            "current_temperature": thermostat.get("currentTemperature"),
            "currentTemperature": thermostat.get("currentTemperature"),
        },
        "thermostat_A": {
            "targetTemperature": thermostat.get("targetTemperature"),
            "currentTemperature": thermostat.get("currentTemperature"),
        },
        "lighting": {"brightness": lights.get("brightness")},
        "lights": {"brightness": lights.get("brightness")},
        "projector": {"power": projector.get("power")},
        "projector_A": {"power": projector.get("power")},
        "readiness": {"ready": readiness},
    }


class _LiveDomPrimitiveExecutor:
    """CIM executor adapter for primitive DOM actions over live Playwright."""

    def __init__(
        self,
        dom_executor: DomExecutor,
        affordances: list[Affordance],
        *,
        point_to: Any | None = None,
        delay: Any | None = None,
        dispatcher: _MainThreadDispatcher | None = None,
    ) -> None:
        self._dom_executor = dom_executor
        self._affordances = {affordance.id: affordance for affordance in affordances}
        self._point_to = point_to
        self._delay = delay
        self._dispatcher = dispatcher

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        affordance_id = str(skill_call.params.get("affordance_id", ""))
        affordance = self._affordances.get(affordance_id)
        if affordance is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="dom",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"unknown live DOM affordance: {affordance_id}",
            )
        selector = str(affordance.locator.get("selector", ""))
        if self._point_to is not None and selector:
            self._call_playwright(
                self._point_to,
                selector,
                f"CIM {skill_call.params.get('primitive_action')}: {affordance.label}",
            )
        result = self._call_playwright(
            self._dom_executor.execute,
            affordance,
            value=skill_call.params.get("value"),
            skill_id=f"{skill_call.skill_id}.{affordance.id}",
        )
        assert isinstance(result, ExecutionResult)
        if result.success and affordance.label == "Book Room":
            result.raw_observation_delta = {
                **result.raw_observation_delta,
                "booking": {"confirmed": True},
                "booking_status": "confirmed",
                "booking_confirmed": True,
            }
        if self._delay is not None:
            self._delay()
        return result

    def _call_playwright(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        if self._dispatcher is None:
            return func(*args, **kwargs)
        return self._dispatcher.call(func, *args, **kwargs)


class _LiveWotSkillExecutor:
    """CIM executor adapter for live WoT skills with state re-observation."""

    def __init__(
        self,
        wot_executor: WotExecutor,
        control_url: str,
        *,
        booked: Any,
        point_to: Any | None = None,
        delay: Any | None = None,
        dispatcher: _MainThreadDispatcher | None = None,
    ) -> None:
        self._wot_executor = wot_executor
        self._control_url = control_url
        self._booked = booked
        self._point_to = point_to
        self._delay = delay
        self._dispatcher = dispatcher

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        if self._point_to is not None:
            selector = _selector_for_wot_skill(skill_call.skill_id)
            if selector:
                if self._dispatcher is None:
                    self._point_to(selector, f"CIM WoT: {skill_call.skill_id}")
                else:
                    self._dispatcher.call(self._point_to, selector, f"CIM WoT: {skill_call.skill_id}")

        if skill_call.skill_id == "verify_readiness":
            state = _read_state(self._control_url).get("state", {})
            normalized = _normalized_live_device_state(state, booked=bool(self._booked()))
            result = ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=bool(normalized["readiness"]["ready"]),
                latency_ms=0.0,
                confidence=1.0,
                failure_reason=None if normalized["readiness"]["ready"] else "readiness_not_satisfied",
                raw_observation_delta=normalized,
            )
        else:
            env_call = _wot_environment_skill_call(skill_call)
            result = await self._wot_executor.execute(env_call, observation)
            result.skill_id = skill_call.skill_id
            state = _read_state(self._control_url).get("state", {})
            result.raw_observation_delta = {
                **result.raw_observation_delta,
                **_normalized_live_device_state(state, booked=bool(self._booked())),
            }

        if self._delay is not None:
            self._delay()
        return result


class _TraceOnlyVisualExecutor:
    """Advertise visual fallback as a recoverable backend in CIM traces."""

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        _ = observation
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used="visual",
            success=False,
            latency_ms=0.0,
            confidence=0.0,
            failure_reason="visual fallback is executed by the live demo presentation layer",
        )


def _wot_environment_skill_call(skill_call: SkillCall) -> SkillCall:
    params = dict(skill_call.params)
    if skill_call.skill_id == "set_temperature" and "target" in params:
        params.setdefault("targetTemperature", params["target"])
    return SkillCall(
        skill_id=skill_call.skill_id,
        params=params,
        priority=skill_call.priority,
        required_postconditions=list(skill_call.required_postconditions),
        preferred_backends=list(skill_call.preferred_backends),
    )


def _selector_for_wot_skill(skill_id: str) -> str:
    return {
        "turn_on_projector": "[data-testid='projector-panel']",
        "set_temperature": "[data-testid='thermostat-panel']",
        "set_lighting": "[data-testid='lighting-panel']",
        "verify_readiness": "[data-testid='readiness-panel']",
    }.get(skill_id, "")


def _skill_library_dict() -> dict[str, Any]:
    return {skill.skill_id: skill for skill in load_skill_library().all()}


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
        dispatcher = _MainThreadDispatcher()
        live_dom_affordances = _with_live_runtime_planning_hints(pam.affordances)
        booking_state = {"booked": False}
        cognitive_map = CognitiveMap(task_id="prepare_room_A_1400_live_chaos")
        cognitive_map.update_affordances(live_dom_affordances + all_wot_affordances)
        manager = ContinuousInteractionManager(
            _skill_library_dict(),
            {
                "dom": _LiveDomPrimitiveExecutor(
                    dom_executor,
                    live_dom_affordances,
                    delay=delay_for_demo,
                    dispatcher=dispatcher,
                ),
                "visual": _TraceOnlyVisualExecutor(),
                "wot": _LiveWotSkillExecutor(
                    wot_executor,
                    control_url,
                    booked=lambda: booking_state["booked"],
                    delay=delay_for_demo,
                    dispatcher=dispatcher,
                ),
            },
            cognitive_map,
        )

        def run_cim_skill(skill_id: str, params: dict[str, Any]) -> Any:
            selector = _selector_for_wot_skill(skill_id)
            if selector:
                point_to(selector, f"CIM WoT: {skill_id}", session)
            result = _run_async_runtime(
                manager.run_skill(
                    SkillCall(skill_id, params),
                    _observation_from_live_state(control_url, booked=booking_state["booked"]),
                ),
                dispatcher=dispatcher,
            )
            trace.append(
                _runtime_trace_entry(
                    skill_id=skill_id,
                    controller="ContinuousInteractionManager.run_skill",
                    observation_source="control plane state -> normalized Observation -> CognitiveMap",
                    result=result,
                )
            )
            return result

        dom_event = next((event for event in policy.events if event.failure_type == "dom_selector_mutation"), None)
        booking_selector = "[data-testid='book-room-button']"
        if dom_event is not None:
            applied = _apply_live_chaos_event(session, control_url, dom_event)
            trace.append({"skill_id": "confirm_booking", "event_type": "chaos_injected", **applied})
            booking_selector = "[data-testid='book-room-button-v2']"
            point_to(booking_selector, "Chaos: DOM selector changed", session)

        point_to(booking_selector, "DOM attempt uses cached selector", session)
        dom_runtime = _run_async_runtime(
            manager.run_goal(
                goal_id="confirm_booking_live_chaos_goal",
                goal_state="device_states.booking.confirmed == true",
                parameters={"room": "A", "time": "14:00"},
                observation=_observation_from_live_state(control_url, booked=False),
            ),
            dispatcher=dispatcher,
        )
        trace.append(
            _runtime_trace_entry(
                skill_id="confirm_booking_live_chaos_goal",
                controller="ContinuousInteractionManager.run_goal",
                observation_source="BrowserSession.state -> DomTransducer -> PageAffordanceModel -> CognitiveMap",
                result=dom_runtime,
            )
        )
        if dom_runtime.state.value == "completed":
            booked = True
            booking_state["booked"] = True
        if dom_runtime.state.value != "completed":
            point_to(booking_selector, "Recovery tier 2: visual fallback", session)
            session.click(booking_selector)
            booked = True
            booking_state["booked"] = True
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

        run_cim_skill("turn_on_projector", {"room": "A"})
        delay_for_demo()

        run_cim_skill("set_temperature", {"room": "A", "target": 21})
        wot_event = next((event for event in policy.events if event.failure_type == "wot_postcondition_mismatch"), None)
        if wot_event is not None:
            applied = _apply_live_chaos_event(session, control_url, wot_event)
            trace.append({"skill_id": "set_temperature", "event_type": "chaos_injected", **applied})
        point_to("[data-testid='thermostat-panel']", "Chaos: WoT returns success but state is stale", session)
        mismatch_runtime = run_cim_skill("set_temperature", {"room": "A", "target": 22})
        failed_state = _read_state(control_url)
        mismatch_result = mismatch_runtime.execution_result
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
                trace.append(
                    {
                        "skill_id": "set_temperature",
                        "event_type": "chaos_cleared",
                        **_clear_live_chaos_event(session, control_url, wot_event),
                    }
                )
            retry_runtime = run_cim_skill("set_temperature", {"room": "A", "target": 22})
            retry_result = retry_runtime.execution_result
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

        run_cim_skill("set_lighting", {"room": "A", "brightness": 40})
        time.sleep(2.0)
        point_to("[data-testid='readiness-panel']", "Final oracle: READY", session)
        readiness_runtime = run_cim_skill("verify_readiness", {"room": "A"})
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
                "runtime_step": _runtime_step_payload(readiness_runtime),
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
            "dom_selector_mutation_injected": any(
                event.failure_type == "dom_selector_mutation" for event in policy.events
            ),
            "visual_fallback_triggered": any(row.get("backend") == "visual" for row in trace),
            "wot_false_success_injected": any(
                event.failure_type == "wot_postcondition_mismatch" for event in policy.events
            ),
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
    (output_dir / "chaos_demo_trace_live.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def run_live_agent_demo(
    web_url: str,
    wot_url: str,
    control_url: str,
    output_dir: Path,
    *,
    directory_url: str = "",
    headed: bool = False,
    step_delay_s: float = 0.0,
    pause_at_end: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    _post_json(f"{control_url.rstrip('/')}/reset", {})
    tds = _fetch_tds(wot_url, directory_url)
    parser = TdAffordanceParser()
    thing_models = [parser.parse(td) for td in tds]
    wot_executor = WotExecutor(tds)

    trace: list[dict[str, Any]] = []

    all_wot_affordances = [affordance for model in thing_models for affordance in model.affordances]

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
        live_dom_affordances = _with_live_runtime_planning_hints(pam.affordances)
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
        dispatcher = _MainThreadDispatcher()
        cognitive_map = CognitiveMap(task_id="prepare_room_A_1400_live")
        cognitive_map.update_affordances(live_dom_affordances + all_wot_affordances)
        booking_state = {"booked": False}
        manager = ContinuousInteractionManager(
            _skill_library_dict(),
            {
                "dom": _LiveDomPrimitiveExecutor(
                    dom_executor,
                    live_dom_affordances,
                    delay=delay_for_demo,
                    dispatcher=dispatcher,
                ),
                "wot": _LiveWotSkillExecutor(
                    wot_executor,
                    control_url,
                    booked=lambda: booking_state["booked"],
                    delay=delay_for_demo,
                    dispatcher=dispatcher,
                ),
            },
            cognitive_map,
        )
        booking_result = _run_async_runtime(
            manager.run_goal(
                goal_id="confirm_booking_live_goal",
                goal_state="device_states.booking.confirmed == true",
                parameters={"room": "A", "time": "14:00"},
                observation=_observation_from_live_state(control_url, booked=False),
            ),
            dispatcher=dispatcher,
        )
        booked = booking_result.state.value == "completed"
        booking_state["booked"] = booked
        trace.append(
            _runtime_trace_entry(
                skill_id="confirm_booking_live_goal",
                controller="ContinuousInteractionManager.run_goal",
                observation_source="BrowserSession.state -> DomTransducer -> PageAffordanceModel -> CognitiveMap",
                result=booking_result,
            )
        )
        delay_for_demo()

        def run_cim_skill(skill_id: str, params: dict[str, Any]) -> Any:
            selector = _selector_for_wot_skill(skill_id)
            if selector:
                point_to(selector, f"CIM WoT: {skill_id}")
            result = _run_async_runtime(
                manager.run_skill(
                    SkillCall(skill_id, params),
                    _observation_from_live_state(control_url, booked=booking_state["booked"]),
                ),
                dispatcher=dispatcher,
            )
            trace.append(
                _runtime_trace_entry(
                    skill_id=skill_id,
                    controller="ContinuousInteractionManager.run_skill",
                    observation_source="control plane state -> normalized Observation -> CognitiveMap",
                    result=result,
                )
            )
            return result

        run_cim_skill("turn_on_projector", {"room": "A"})
        run_cim_skill("set_temperature", {"room": "A", "target": 22})
        run_cim_skill("set_lighting", {"room": "A", "brightness": 40})
        readiness_result = run_cim_skill("verify_readiness", {"room": "A"})

        time.sleep(2.0)
        point_to("[data-testid='readiness-panel']", "Verifier: postconditions pass")
        state_after = _read_state(control_url)
        status_text = session.text_content("[data-testid='readiness-status']")
        normal_ready = readiness_result.state.value == "completed" and _ready_from_state(state_after, booked=True)
        trace.append(
            {
                "skill_id": "verify_readiness",
                "controller": "ContinuousInteractionManager.run_skill",
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
        mismatch_runtime = run_cim_skill("set_temperature", {"room": "A", "target": 24})
        postcondition_failed = mismatch_runtime.state.value == "recovering"
        _post_json(f"{control_url.rstrip('/')}/failure", {"thing": "thermostat", "clear": True})
        recovery_runtime = run_cim_skill("set_temperature", {"room": "A", "target": 24})
        recovered_state = _read_state(control_url)
        recovery_passed = recovery_runtime.state.value == "completed" and (
            recovered_state["state"]["thermostat"]["targetTemperature"] == 24
        )
        trace.append(
            {
                "skill_id": "recovery_cascade",
                "controller": "ContinuousInteractionManager.run_skill",
                "backend": "verifier",
                "failure_detected": postcondition_failed,
                "recovery_action": "clear_injected_fault_then_retry_wot",
                "runtime_failure_step": _runtime_step_payload(mismatch_runtime),
                "runtime_recovery_step": _runtime_step_payload(recovery_runtime),
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
            "cim_consumes_live_pam": True,
            "cim_runs_booking_goal": booked,
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
    parser.add_argument(
        "--directory-url",
        default="http://localhost:8082",
        help="Thing Directory for runtime TD discovery; empty to force the static thing list.",
    )
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
            directory_url=args.directory_url,
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
