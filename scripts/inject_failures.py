"""Controlled failure-injection driver (Member B — Chaos Monkey, advisor §11).

Single source of truth mapping each perturbation to the recovery tier it should
trigger (Table B4 "expected behaviour"). Member C asserts the *observed* tier
against this map. Pure-data so it unit-tests offline; ``--apply`` posts the WoT
faults to the node-wot control plane (:8081) and prints the DOM faults to set on
the dashboard URL.

Tier legend: 1=retry · 2=reroute · 3=rollback+replan · 4=escalate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Where each fault is injected: "wot" → control plane, "dom" → dashboard hook.
WOT = "wot"
DOM = "dom"


@dataclass(frozen=True)
class FailureSpec:
    failure_type: str
    side: str  # WOT | DOM
    expected_tier: str
    expected_behavior: str
    control_payload: dict[str, Any] | None = None


# Canonical perturbation catalogue (advisor §9 + environment_demo §B2/B4).
CATALOGUE: list[FailureSpec] = [
    FailureSpec("visual_misclick", DOM, "1", "retry same backend resolves transient misclick",
                {"fault": "visual_misclick"}),
    FailureSpec("dom_selector_mutation", DOM, "2", "DOM selector fails → reroute to Visual SoM",
                {"fault": "selector_mutation"}),
    FailureSpec("layout_shift", DOM, "2", "moved control → reroute to Visual SoM",
                {"fault": "layout_shift"}),
    FailureSpec("wot_timeout", WOT, "2", "WoT times out → reroute to DOM dashboard fallback",
                {"type": "timeout", "delay_ms": 1500}),
    FailureSpec("postcondition_mismatch", WOT, "3", "HTTP 200 but state unchanged → rollback + replan",
                {"type": "postcondition_mismatch"}),
    FailureSpec("backend_offline", WOT, "2->4", "reroute; escalate if no backend remains",
                {"type": "offline"}),
    FailureSpec("malformed_td", WOT, "2->4", "malformed TD → reroute / escalate",
                {"type": "malformed"}),
    FailureSpec("perceptual_conflict", WOT, "arb->4", "dashboard booked vs sensor occupied → arbitrate, escalate",
                {"type": "postcondition_mismatch"}),
]

_BY_TYPE = {spec.failure_type: spec for spec in CATALOGUE}


def expected_tier(failure_type: str) -> str:
    return _BY_TYPE[failure_type].expected_tier


def robustness_plan() -> list[dict[str, Any]]:
    """Table B4 rows: failure_type, side, expected tier, expected behaviour."""
    return [
        {
            "failure_type": s.failure_type,
            "side": s.side,
            "expected_tier": s.expected_tier,
            "expected_behavior": s.expected_behavior,
        }
        for s in CATALOGUE
    ]


def apply(failure_type: str, *, thing: str = "thermostat", control_url: str = "http://localhost:8081") -> dict[str, Any]:
    """Activate one WoT fault on the live control plane (lazy httpx)."""
    spec = _BY_TYPE[failure_type]
    if spec.side != WOT:
        return {"side": DOM, "dashboard_query": spec.control_payload, "note": "set via dashboard URL / window.__injectFault"}
    import httpx  # lazy

    payload = {"thing": thing, **(spec.control_payload or {})}
    resp = httpx.post(f"{control_url}/failure", json=payload, timeout=3.0)
    return {"side": WOT, "posted": payload, "status": resp.status_code}


def reset(control_url: str = "http://localhost:8081") -> int:
    import httpx  # lazy

    return httpx.post(f"{control_url}/reset", timeout=3.0).status_code


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(robustness_plan(), indent=2))
