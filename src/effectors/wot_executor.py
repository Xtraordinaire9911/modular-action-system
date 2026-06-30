"""WoT executor using TD-derived href/method bindings."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Callable

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.perception.td_affordance_parser import StateAssertionSource, TdAffordanceParser
from src.perception.wot_security import SecurityScheme, build_auth

try:
    import httpx  # type: ignore

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

SendFn = Callable[..., tuple[int, Any]]

_SKILL_TO_AFFORDANCE_ID = {
    "turn_on_projector": "wot_projector_A_setPower",
    "set_temperature": "wot_thermostat_A_setTargetTemperature",
    "set_lighting": "wot_lights_A_setBrightness",
    "verify_readiness": "wot_readiness_check",
}
_SKILL_TO_AFFORDANCE_SUFFIX = {
    "turn_on_projector": "setPower",
    "set_temperature": "setTargetTemperature",
    "set_lighting": "setBrightness",
}
_SKILL_TO_PAYLOAD_KEY = {
    "turn_on_projector": "power",
    "set_temperature": "target",
    "set_lighting": "brightness",
}
_SKILL_TO_STATE_SPEC = {
    "turn_on_projector": ("projector", "power", "power", "on"),
    "set_temperature": ("thermostat", "target_temperature", "targetTemperature", "target"),
    "set_lighting": ("lighting", "brightness", "brightness", "brightness"),
    "verify_readiness": ("readiness", "ready", "ready", True),
}


class RateLimitExceeded(RuntimeError):
    """Raised when a synchronous call would breach a TD-declared interval."""


class _MinIntervalGate:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._last: dict[str, float] = {}
        self._clock = clock

    def check(self, thing_id: str, min_interval_ms: float) -> None:
        if min_interval_ms <= 0:
            return
        now = self._clock()
        last = self._last.get(thing_id)
        if last is not None and (now - last) * 1000.0 < min_interval_ms:
            raise RateLimitExceeded(f"{thing_id}: min interval {min_interval_ms:.0f}ms not elapsed")
        self._last[thing_id] = now


class _AsyncRateLimiter:
    def __init__(self, min_interval_s: float = 0.1) -> None:
        self._last: dict[str, float] = defaultdict(float)
        self._interval = min_interval_s

    async def acquire(self, thing_id: str) -> None:
        elapsed = time.monotonic() - self._last[thing_id]
        if elapsed < self._interval:
            await asyncio.sleep(self._interval - elapsed)
        self._last[thing_id] = time.monotonic()


def _httpx_send(method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
    if not _HTTPX_AVAILABLE:
        raise RuntimeError("httpx not installed")
    timeout = kwargs.pop("timeout_s", 2.0)
    response = httpx.request(method, url, timeout=timeout, **kwargs)
    content_type = response.headers.get("content-type", "")
    body: Any = response.json() if "application/json" in content_type else response.text
    return response.status_code, body


class WotExecutor:
    backend = "wot"

    def __init__(
        self,
        tds: list[dict[str, Any]] | None = None,
        *,
        send: SendFn | None = None,
        credentials: dict[str, str] | None = None,
        security_by_thing: dict[str, SecurityScheme] | None = None,
        timeout_ms: int = 2000,
        timeout_s: float | None = None,
        gate: _MinIntervalGate | None = None,
    ) -> None:
        self._send = send or _httpx_send
        self._credentials = credentials or {}
        self._security = security_by_thing or {}
        self._timeout_ms = int((timeout_s * 1000) if timeout_s is not None else timeout_ms)
        self._affordances: dict[str, Affordance] = {}
        self._state_sources: dict[tuple[str, str], StateAssertionSource] = {}
        self._gate = gate or _MinIntervalGate()
        self._async_rate_limiter = _AsyncRateLimiter()
        if tds:
            self.load_tds(tds)

    def execute(
        self,
        target: Affordance | SkillCall,
        observation: Observation | None = None,
        *,
        value: Any | None = None,
        skill_id: str = "",
    ) -> ExecutionResult | Any:
        if isinstance(target, SkillCall):
            return self._execute_skill(target, observation or Observation())
        return self._execute_affordance(target, value=value, skill_id=skill_id)

    def _execute_affordance(self, affordance: Affordance, *, value: Any | None, skill_id: str = "") -> ExecutionResult:
        start = time.perf_counter()
        try:
            delta = self._run_affordance(affordance, value)
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=True,
                latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
                confidence=affordance.confidence,
                raw_observation_delta=delta,
            )
        except Exception as exc:
            return ExecutionResult(
                skill_id=skill_id or affordance.id,
                backend_used=self.backend,
                success=False,
                latency_ms=round((time.perf_counter() - start) * 1000.0, 3),
                confidence=affordance.confidence,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

    def _run_affordance(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        href = affordance.locator.get("href")
        method = str(affordance.locator.get("method", "GET")).upper()
        thing_id = str(affordance.locator.get("thing_id", affordance.id))
        if not href:
            raise ValueError(f"{affordance.id}: TD form has no href")

        rate = affordance.state.get("rate_limit") or {}
        self._gate.check(thing_id, float(rate.get("min_interval_ms", 0.0)))

        scheme = self._security.get(thing_id)
        headers, params = build_auth(scheme, self._credentials.get(thing_id))
        content_type = affordance.state.get("content_type", "application/json")
        if content_type:
            headers = {**headers, "Content-Type": str(content_type)}
        body = None if affordance.action == "read_property" else value
        status, parsed = self._send(
            method,
            href,
            json=body,
            headers=headers,
            params=params,
            timeout_s=self._timeout_ms / 1000.0,
        )
        if status >= 400:
            raise RuntimeError(f"WoT {method} {href} returned HTTP {status}")
        if affordance.action == "read_property":
            return {"thing_id": thing_id, "property": affordance.label, "value": parsed}
        return {"thing_id": thing_id, "action": affordance.label, "result": parsed, "sent": value}

    def load_tds(self, tds: list[dict[str, Any]]) -> None:
        parser = TdAffordanceParser()
        for td in tds:
            model = parser.parse(td)
            for affordance in model.affordances:
                self._affordances[affordance.id] = affordance
            for source in model.state_sources:
                self._state_sources[(source.thing_id, source.property)] = source
            if model.security is not None:
                self._security.setdefault(model.thing_id, model.security)

    def get_affordance(self, aff_id: str) -> Affordance | None:
        return self._affordances.get(aff_id)

    async def probe_availability(self) -> bool:
        if not _HTTPX_AVAILABLE or not (self._affordances or self._state_sources):
            return False
        href = ""
        if self._affordances:
            href = next(iter(self._affordances.values())).locator.get("href", "")
        elif self._state_sources:
            href = next(iter(self._state_sources.values())).href
        if not href:
            return False
        base = "/".join(href.split("/")[:3])
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(base)
            return response.status_code < 500
        except Exception:
            return False

    async def _execute_skill(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        start = time.monotonic()
        if skill_call.skill_id == "verify_readiness":
            delta, actual_value, expected_value = await self._build_readiness_delta(observation)
            if actual_value != expected_value:
                return self._skill_failure(skill_call, start, "postcondition_mismatch", delta)
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=True,
                latency_ms=(time.monotonic() - start) * 1000.0,
                confidence=1.0,
                raw_observation_delta=delta,
            )

        affordance_id, affordance = self._resolve_affordance(skill_call.skill_id)
        if affordance_id is None or affordance is None:
            return self._skill_failure(
                skill_call, start, f"no WoT affordance mapping for skill '{skill_call.skill_id}'"
            )
        if not _HTTPX_AVAILABLE:
            return self._skill_failure(skill_call, start, "httpx not installed")

        payload_key = _SKILL_TO_PAYLOAD_KEY.get(skill_call.skill_id)
        payload: Any = None
        if payload_key and payload_key in skill_call.params:
            payload = skill_call.params[payload_key]
        elif skill_call.skill_id == "turn_on_projector":
            payload = "on"

        thing_id = str(affordance.locator.get("thing_id", "unknown"))
        href = str(affordance.locator.get("href", ""))
        method = str(affordance.locator.get("method", "POST")).upper()
        try:
            await self._async_rate_limiter.acquire(thing_id)
            async with httpx.AsyncClient(timeout=self._timeout_ms / 1000.0) as client:
                response = await client.request(method, href, json=payload if method != "GET" else None)
            latency = (time.monotonic() - start) * 1000.0
            if response.status_code >= 400:
                return self._skill_failure(skill_call, start, f"HTTP {response.status_code}: {response.text[:200]}")
            delta, actual_value, expected_value = await self._build_skill_delta(skill_call, thing_id)
            if actual_value != expected_value:
                return self._skill_failure(skill_call, start, "postcondition_mismatch", delta)
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=True,
                latency_ms=latency,
                confidence=1.0,
                raw_observation_delta=delta,
            )
        except Exception as exc:
            return self._skill_failure(skill_call, start, str(exc))

    def _resolve_affordance(self, skill_id: str) -> tuple[str | None, Affordance | None]:
        affordance_id = _SKILL_TO_AFFORDANCE_ID.get(skill_id)
        if affordance_id is not None:
            affordance = self._affordances.get(affordance_id)
            if affordance is not None:
                return affordance_id, affordance

        suffix = _SKILL_TO_AFFORDANCE_SUFFIX.get(skill_id)
        if suffix is None:
            return None, None
        for loaded_id, affordance in self._affordances.items():
            if loaded_id.endswith(f"_{suffix}") or affordance.label == suffix:
                return loaded_id, affordance
        return None, None

    async def _build_readiness_delta(self, observation: Observation) -> tuple[dict[str, Any], Any, Any]:
        readiness = observation.device_states.get("readiness")
        actual_value = None
        if isinstance(readiness, dict):
            actual_value = bool(readiness.get("ready"))
        elif readiness is not None:
            actual_value = bool(readiness)
        return {"readiness": {"ready": actual_value}}, actual_value, True

    async def _build_skill_delta(self, skill_call: SkillCall, thing_id: str) -> tuple[dict[str, Any], Any, Any]:
        state_spec = _SKILL_TO_STATE_SPEC.get(skill_call.skill_id)
        if state_spec is None:
            return {}, None, None

        root, attribute, property_name, expected_key = state_spec
        expected_value = skill_call.params.get(expected_key, expected_key)
        actual_raw = await self.read_property(thing_id, property_name)
        actual_value: Any
        if skill_call.skill_id == "verify_readiness":
            if isinstance(actual_raw, dict):
                actual_value = bool(actual_raw.get("ready"))
            else:
                actual_value = bool(actual_raw)
        else:
            if isinstance(actual_raw, dict):
                actual_value = actual_raw.get(property_name, actual_raw.get(attribute, actual_raw))
            else:
                actual_value = actual_raw
        return {root: {attribute: actual_value}}, actual_value, expected_value

    def _skill_failure(
        self, skill_call: SkillCall, start: float, reason: str, delta: dict[str, Any] | None = None
    ) -> ExecutionResult:
        return ExecutionResult(
            skill_id=skill_call.skill_id,
            backend_used=self.backend,
            success=False,
            latency_ms=(time.monotonic() - start) * 1000.0,
            confidence=0.0,
            failure_reason=reason,
            raw_observation_delta=delta or {},
        )

    async def read_property(self, thing_id: str, property_name: str) -> Any:
        if not _HTTPX_AVAILABLE:
            return None
        source = self._state_sources.get((thing_id, property_name))
        affordance = self._affordances.get(f"wot_{thing_id}_{property_name}")
        href = source.href if source else (affordance.locator.get("href", "") if affordance else "")
        if not href:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout_ms / 1000.0) as client:
                response = await client.get(href)
            if response.status_code < 400:
                return response.json()
        except Exception:
            pass
        return None
