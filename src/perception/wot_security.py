"""WoT securityDefinitions + rate-limit parsing (advisor §4.3, §14.1).

The assessment requires that the agent *dynamically* extract the
``securityDefinitions`` block (to handle API keys / tokens) and the polling /
rate-limit constraints declared in a Thing Description — rather than hard-coding
them. This module turns those declarations into:

  * an auth header/query builder the WoT executor applies per request, and
  * a normalised ``RateLimit`` (requests per window) the executor / rate limiter
    enforces so a recovering agent never DDoS-es the building network.

Pure-stdlib and side-effect free, so it is fully unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WINDOW_SECONDS = {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600, "hour": 3600}
_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*([a-z]+)\s*$", re.IGNORECASE)


@dataclass
class SecurityScheme:
    """A resolved security scheme extracted from ``securityDefinitions``."""

    name: str
    scheme: str  # nosec | basic | bearer | apikey | digest | oauth2
    location: str = "header"  # header | query | body | cookie
    field_name: str = "Authorization"

    @property
    def requires_credential(self) -> bool:
        return self.scheme not in ("nosec",)


@dataclass
class RateLimit:
    """Normalised polling / invocation budget for one affordance or Thing."""

    max_requests: int
    window_seconds: int

    @property
    def min_interval_ms(self) -> float:
        if self.max_requests <= 0:
            return float("inf")
        return (self.window_seconds * 1000.0) / self.max_requests


def parse_rate_limit(raw: Any) -> RateLimit | None:
    """Parse '10/min', {'max':10,'window':'min'} or {'max_requests':..,'window_seconds':..}."""
    if raw is None:
        return None
    if isinstance(raw, str):
        m = _RATE_RE.match(raw)
        if not m:
            return None
        count, unit = int(m.group(1)), m.group(2).lower()
        window = _WINDOW_SECONDS.get(unit) or _WINDOW_SECONDS.get(unit.rstrip("s"))
        return RateLimit(count, window) if window and count > 0 else None
    if isinstance(raw, dict):
        if "window_seconds" in raw and "max_requests" in raw:
            return (
                RateLimit(int(raw["max_requests"]), int(raw["window_seconds"]))
                if int(raw["max_requests"]) > 0 and int(raw["window_seconds"]) > 0
                else None
            )
        if "max" in raw and "window" in raw:
            unit = str(raw["window"]).lower()
            window = _WINDOW_SECONDS.get(unit, _WINDOW_SECONDS.get(unit.rstrip("s"), 0))
            return RateLimit(int(raw["max"]), window) if window and int(raw["max"]) > 0 else None
    return None


def parse_security_definitions(td: dict[str, Any]) -> dict[str, SecurityScheme]:
    """Map every entry of ``securityDefinitions`` into a typed SecurityScheme."""
    out: dict[str, SecurityScheme] = {}
    raw_defs = td.get("securityDefinitions") or {}
    if not isinstance(raw_defs, dict):
        return out
    for name, definition in raw_defs.items():
        scheme = str(definition.get("scheme", "nosec")).lower()
        location = str(definition.get("in", "header")).lower()
        field_name = definition.get("name") or ("Authorization" if location == "header" else "access_token")
        out[name] = SecurityScheme(name=name, scheme=scheme, location=location, field_name=field_name)
    return out


def active_scheme(td: dict[str, Any], schemes: dict[str, SecurityScheme]) -> SecurityScheme | None:
    """Resolve the Thing-level ``security`` reference to a concrete scheme."""
    sec = td.get("security")
    names = [sec] if isinstance(sec, str) else list(sec or [])
    for name in names:
        if name in schemes:
            return schemes[name]
    return next(iter(schemes.values()), None)


def build_auth(scheme: SecurityScheme | None, credential: str | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return (headers, query_params) carrying the credential for one request."""
    if scheme is None or not scheme.requires_credential or not credential:
        return {}, {}
    if scheme.scheme == "bearer":
        return {scheme.field_name: f"Bearer {credential}"}, {}
    if scheme.scheme == "basic":
        return {scheme.field_name: f"Basic {credential}"}, {}
    if scheme.scheme == "apikey":
        if scheme.location == "query":
            return {}, {scheme.field_name: credential}
        return {scheme.field_name: credential}, {}
    return {scheme.field_name: credential}, {}
