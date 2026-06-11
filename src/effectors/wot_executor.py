"""WoT executor — invokes IoT affordances via runtime-parsed TD forms.

Critical constraint (advisor §4, §14.1): **no hard-coded endpoints**. Every
request uses the ``href`` + ``method`` the TD parser extracted into
``affordance.locator``, applies the credential dictated by the parsed
``securityDefinitions``, and respects the per-Thing rate limit so a recovering
agent cannot flood the building network.

The HTTP call is injected as a ``send`` callable so the executor unit-tests with
a fake transport; the default lazily uses ``httpx``.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from src.contracts.types import Affordance
from src.effectors.base import ExecutorBase
from src.perception.wot_security import SecurityScheme, build_auth

# send(method, url, json=, headers=, params=, timeout_s=) -> (status_code, body)
SendFn = Callable[..., tuple[int, Any]]


class RateLimitExceeded(RuntimeError):
    """Raised when a call would breach the TD-declared polling budget."""


class _MinIntervalGate:
    """Per-Thing minimum-interval gate derived from TD rate-limit metadata."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._last: dict[str, float] = {}
        self._clock = clock

    def check(self, thing_id: str, min_interval_ms: float) -> None:
        if min_interval_ms <= 0:
            return
        now = self._clock()
        last = self._last.get(thing_id)
        if last is not None and (now - last) * 1000.0 < min_interval_ms:
            raise RateLimitExceeded(
                f"{thing_id}: min interval {min_interval_ms:.0f}ms not elapsed"
            )
        self._last[thing_id] = now


def _httpx_send(method: str, url: str, **kw: Any) -> tuple[int, Any]:
    import httpx  # lazy: only needed for real I/O

    timeout = kw.pop("timeout_s", 2.0)
    resp = httpx.request(method, url, timeout=timeout, **kw)
    ctype = resp.headers.get("content-type", "")
    body: Any = resp.json() if "application/json" in ctype else resp.text
    return resp.status_code, body


class WotExecutor(ExecutorBase):
    backend = "wot"

    def __init__(
        self,
        *,
        send: SendFn | None = None,
        credentials: dict[str, str] | None = None,
        security_by_thing: dict[str, SecurityScheme] | None = None,
        timeout_ms: int = 2000,
        gate: _MinIntervalGate | None = None,
    ) -> None:
        self._send = send or _httpx_send
        self._credentials = credentials or {}
        self._security = security_by_thing or {}
        self._timeout_ms = timeout_ms
        self._gate = gate or _MinIntervalGate()

    def _run(self, affordance: Affordance, value: Any | None) -> dict[str, Any]:
        href = affordance.locator.get("href")
        method = affordance.locator.get("method", "GET").upper()
        thing_id = affordance.locator.get("thing_id", affordance.id)
        if not href:
            raise ValueError(f"{affordance.id}: TD form has no href (malformed Thing Description)")

        rate = affordance.state.get("rate_limit") or {}
        self._gate.check(thing_id, float(rate.get("min_interval_ms", 0.0)))

        scheme = self._security.get(thing_id)
        headers, params = build_auth(scheme, self._credentials.get(thing_id))
        content_type = affordance.state.get("content_type", "application/json")
        if content_type:
            headers = {**headers, "Content-Type": content_type}

        body = None if affordance.action in ("read_property",) else value
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
