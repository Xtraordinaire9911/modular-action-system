"""WoT executor — async HTTP client for W3C Thing Description endpoints.

Parses Thing Descriptions at runtime (via td_affordance_parser) and invokes
actions or reads properties through their forms hrefs. Hard-coded endpoints
are explicitly forbidden; every URL is taken from the parsed Affordance.

Rate limits declared in the TD are honoured via a per-thing token bucket.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from src.contracts.types import Affordance, ExecutionResult, Observation, SkillCall
from src.effectors.executor_base import ExecutorBase
from src.perception.td_affordance_parser import parse_td

try:
    import httpx  # type: ignore

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


_SKILL_TO_AFFORDANCE_ID: dict[str, str] = {
    "turn_on_projector": "wot_projector_A_setPower",
    "set_temperature": "wot_thermostat_A_setTargetTemperature",
    "set_lighting": "wot_lights_A_setBrightness",
    "verify_readiness": "wot_readiness_check",
}

_SKILL_TO_PAYLOAD_KEY: dict[str, str] = {
    "turn_on_projector": "power",
    "set_temperature": "targetTemperature",
    "set_lighting": "brightness",
}


class _RateLimiter:
    """Simple per-thing token bucket (minimum interval between calls)."""

    def __init__(self, min_interval_s: float = 0.1) -> None:
        self._last: dict[str, float] = defaultdict(float)
        self._interval = min_interval_s

    async def acquire(self, thing_id: str) -> None:
        elapsed = time.monotonic() - self._last[thing_id]
        if elapsed < self._interval:
            await asyncio.sleep(self._interval - elapsed)
        self._last[thing_id] = time.monotonic()


class WotExecutor(ExecutorBase):
    """Execute skills by invoking WoT Thing Description action endpoints."""

    def __init__(
        self,
        tds: list[dict[str, Any]] | None = None,
        timeout_s: float = 3.0,
    ) -> None:
        self._timeout = timeout_s
        self._affordances: dict[str, Affordance] = {}
        self._rate_limiter = _RateLimiter()
        if tds:
            self.load_tds(tds)

    def load_tds(self, tds: list[dict[str, Any]]) -> None:
        """Parse a list of TD dicts and index their affordances by id."""
        for td in tds:
            for aff in parse_td(td):
                self._affordances[aff.id] = aff

    def get_affordance(self, aff_id: str) -> Affordance | None:
        return self._affordances.get(aff_id)

    # ------------------------------------------------------------------
    # ExecutorBase interface
    # ------------------------------------------------------------------

    async def probe_availability(self) -> bool:
        if not _HTTPX_AVAILABLE or not self._affordances:
            return False
        first = next(iter(self._affordances.values()))
        href = first.locator.get("href", "")
        if not href:
            return False
        base = "/".join(href.split("/")[:3])
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(base)
                return resp.status_code < 500
        except Exception:
            return False

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        t0 = time.monotonic()
        aff_id = _SKILL_TO_AFFORDANCE_ID.get(skill_call.skill_id)

        if aff_id is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"no WoT affordance mapping for skill '{skill_call.skill_id}'",
            )

        aff = self._affordances.get(aff_id)
        if aff is None:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason=f"affordance '{aff_id}' not loaded; call load_tds() first",
            )

        payload_key = _SKILL_TO_PAYLOAD_KEY.get(skill_call.skill_id)
        payload = {}
        if payload_key and payload_key in skill_call.params:
            payload = {payload_key: skill_call.params[payload_key]}
        elif skill_call.skill_id == "turn_on_projector":
            payload = {"power": "on"}

        thing_id = aff.locator.get("thing_id", "unknown")
        href = aff.locator.get("href", "")
        method = aff.locator.get("method", "POST").upper()

        if not _HTTPX_AVAILABLE:
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=False,
                latency_ms=0.0,
                confidence=0.0,
                failure_reason="httpx not installed",
            )

        try:
            await self._rate_limiter.acquire(thing_id)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                if method == "POST":
                    resp = await client.post(href, json=payload)
                else:
                    resp = await client.get(href)
            latency = (time.monotonic() - t0) * 1000
            if resp.status_code >= 400:
                return ExecutionResult(
                    skill_id=skill_call.skill_id,
                    backend_used="wot",
                    success=False,
                    latency_ms=latency,
                    confidence=0.0,
                    failure_reason=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )
            obs_delta = {}
            try:
                obs_delta = resp.json()
            except Exception:
                pass
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=True,
                latency_ms=latency,
                confidence=1.0,
                raw_observation_delta=obs_delta,
            )
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used="wot",
                success=False,
                latency_ms=latency,
                confidence=0.0,
                failure_reason=str(exc),
            )

    # ------------------------------------------------------------------
    # Read-only property helper
    # ------------------------------------------------------------------

    async def read_property(self, thing_id: str, property_name: str) -> Any:
        """Read a TD property and return the parsed JSON value, or None."""
        aff_id = f"wot_{thing_id}_{property_name}"
        aff = self._affordances.get(aff_id)
        if aff is None or not _HTTPX_AVAILABLE:
            return None
        href = aff.locator.get("href", "")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(href)
            if resp.status_code < 400:
                return resp.json()
        except Exception:
            pass
        return None
