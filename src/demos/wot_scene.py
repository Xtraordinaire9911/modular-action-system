"""A WoT device scene for the demo, and the failure the review cares most about.

Two gaps this closes.

The project's headline claim is that one action system drives three surfaces -
DOM, WoT and visual - yet the loop demo only ever showed DOM and visual. For a
smart-room project that is the home surface missing from the demonstration.

And the failure mode the review singled out was never shown: *the API returns
success, but the device state does not change*. Every fault the demo injected
so far made the action visibly fail, which is the easy case. A backend that
reports success while nothing happened is the case that separates an agent that
verifies from a script that assumes.

Runs entirely in-process against a fake servient built from the project's real
Thing Descriptions, so no Docker is needed and the demo stays runnable anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TD_DIR = REPO_ROOT / "config" / "wot_td"


class FakeServient:
    """An in-memory device endpoint driven by the project's own TDs.

    ``silent_failure`` is the interesting one: the write is acknowledged with a
    2xx exactly as a healthy device would, and the stored state is left alone.
    Nothing in the response reveals the problem, so only re-reading the property
    can detect it.
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {"targetTemperature": 18, "currentTemperature": 18})
        self.silent_failure = False
        self.offline = False
        self.calls: list[tuple[str, str, Any]] = []

    def send(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        prop = url.rsplit("/", 1)[-1]
        body = kwargs.get("json")
        self.calls.append((method, prop, body))
        if self.offline:
            raise ConnectionError("device unreachable")
        if method.upper() == "GET":
            return 200, self.state.get(prop)
        if self.silent_failure:
            return 204, None  # acknowledged, and quietly ignored
        self.state[prop] = body
        return 204, None


def load_thermostat_td() -> dict[str, Any]:
    """The project's real thermostat TD, pointed at a local base URL."""
    td = json.loads((TD_DIR / "thermostat.td.json").read_text(encoding="utf-8"))
    td["base"] = "http://127.0.0.1:9/thermostat"  # never dialled; FakeServient answers
    return td


@dataclass
class WotStep:
    phase: str
    detail: str
    ok: bool = True


@dataclass
class WotOutcome:
    """What happened, in the shape the narration and the trajectory both want."""

    goal: str
    steps: list[WotStep] = field(default_factory=list)
    recovered: bool = False
    tiers_used: list[int] = field(default_factory=list)
    silent_failure_caught: bool = False

    def add(self, phase: str, detail: str, ok: bool = True) -> None:
        self.steps.append(WotStep(phase, detail, ok))

    def failures(self) -> list[WotStep]:
        return [s for s in self.steps if not s.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "recovered": self.recovered,
            "tiers_used": list(self.tiers_used),
            "silent_failure_caught": self.silent_failure_caught,
            "steps": [{"phase": s.phase, "detail": s.detail, "ok": s.ok} for s in self.steps],
        }


def perceive_device(td: dict[str, Any]) -> list[Any]:
    """Parse the Thing Description into property endpoints at runtime.

    Deliberately goes through the project's own TD parser rather than a device
    map written by hand: the endpoints, methods and read-only flags all come
    from the description the device publishes. This is the WoT counterpart of
    transducing a page, and it is what lets one planner drive a web page and a
    thermostat without knowing the difference.
    """
    from src.perception.td_affordance_parser import TdAffordanceParser

    return list(TdAffordanceParser().parse(td).state_sources)


def read_property(send: Any, source: Any) -> Any:
    """Read one device property through the form the TD declares for reading."""
    status, value = send(source.method or "GET", source.href)
    if status >= 400:
        raise RuntimeError(f"read {source.property} returned HTTP {status}")
    return value


def write_property(send: Any, source: Any, value: Any) -> None:
    """Write the property, using the same href with PUT (the common WoT shape)."""
    status, _ = send("PUT", source.href, json=value)
    if status >= 400:
        raise RuntimeError(f"write {source.property} returned HTTP {status}")


def verify_device(send: Any, source: Any, expected: Any) -> bool:
    """Re-read the property and compare against what was requested.

    This is the whole point of the scene. The write returned 2xx; that is not
    evidence. Only reading the state back tells us whether it took effect.
    """
    return read_property(send, source) == expected


__all__ = [
    "FakeServient",
    "WotOutcome",
    "WotStep",
    "load_thermostat_td",
    "perceive_device",
    "read_property",
    "verify_device",
    "write_property",
]
