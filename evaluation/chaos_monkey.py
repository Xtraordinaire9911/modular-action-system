"""Deterministic chaos injection helpers for robustness evaluation."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Literal

from src.contracts.types import ExecutionResult, Observation, SkillCall

ChaosTiming = Literal["before_skill", "after_success", "during_verification", "between_recovery_attempts"]


@dataclass(frozen=True)
class ChaosEvent:
    event_id: str
    failure_type: str
    target: str
    timing: ChaosTiming
    skill_id: str | None = None
    severity: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChaosPolicy:
    seed: int
    events: list[ChaosEvent]
    name: str = "seeded"

    @classmethod
    def seeded(cls, seed: int, *, level: int = 3) -> "ChaosPolicy":
        rng = random.Random(seed)
        candidates = [
            ("wot_timeout", "wot", "before_skill", "set_temperature"),
            ("wot_offline", "wot", "before_skill", "turn_on_projector"),
            ("wot_malformed", "wot", "during_verification", "set_temperature"),
            ("wot_postcondition_mismatch", "wot", "after_success", "set_temperature"),
            ("dom_selector_mutation", "dom", "before_skill", "confirm_booking"),
            ("dom_layout_shift", "dom", "before_skill", "confirm_booking"),
            ("dom_stale_temperature", "dom", "during_verification", "set_temperature"),
            ("dom_disabled_button", "dom", "before_skill", "confirm_booking"),
        ]
        n_events = 0 if level <= 1 else (1 if level == 2 else 2)
        events: list[ChaosEvent] = []
        for index, (failure_type, target, timing, skill_id) in enumerate(rng.sample(candidates, k=n_events), start=1):
            events.append(
                ChaosEvent(
                    event_id=f"chaos_{seed}_{index}",
                    failure_type=failure_type,
                    target=target,
                    timing=timing,  # type: ignore[arg-type]
                    skill_id=skill_id,
                    severity=2 if "postcondition" in failure_type or "offline" in failure_type else 1,
                )
            )
        return cls(seed=seed, events=events, name=f"level_{level}_seed_{seed}")

    def events_for(self, timing: ChaosTiming, skill_id: str) -> list[ChaosEvent]:
        return [
            event
            for event in self.events
            if event.timing == timing and (event.skill_id is None or event.skill_id == skill_id)
        ]


class OfflineChaosExecutor:
    """Wrap an executor and apply chaos events without requiring live services."""

    def __init__(self, backend: str, wrapped: Any, policy: ChaosPolicy) -> None:
        self.backend = backend
        self._wrapped = wrapped
        self._policy = policy
        self.events_applied: list[ChaosEvent] = []

    async def execute(self, skill_call: SkillCall, observation: Observation) -> ExecutionResult:
        before = self._first_matching("before_skill", skill_call)
        if before is not None:
            self.events_applied.append(before)
            return _failure_result(skill_call, self.backend, before)

        result = await self._wrapped.execute(skill_call, observation)
        after = self._first_matching("after_success", skill_call)
        if after is not None and result.success:
            self.events_applied.append(after)
            stale_delta = _stale_delta_for(skill_call, observation)
            return ExecutionResult(
                skill_id=skill_call.skill_id,
                backend_used=self.backend,
                success=True,
                latency_ms=result.latency_ms,
                confidence=result.confidence,
                failure_reason=None,
                raw_observation_delta=stale_delta,
            )
        return result

    def _first_matching(self, timing: ChaosTiming, skill_call: SkillCall) -> ChaosEvent | None:
        for event in self._policy.events_for(timing, skill_call.skill_id):
            if event.target == self.backend and event not in self.events_applied:
                return event
        return None


def apply_observation_chaos(
    observation: Observation,
    policy: ChaosPolicy,
    *,
    timing: ChaosTiming,
    skill_id: str,
) -> Observation:
    """Return an observation copy with deterministic verification-time faults."""
    events = policy.events_for(timing, skill_id)
    if not events:
        return observation
    device_states = dict(observation.device_states)
    tree = dict(observation.accessibility_tree or {})
    page_state = dict(tree.get("page_state") or {})
    for event in events:
        if event.failure_type == "dom_stale_temperature":
            page_state["thermostat"] = {"target_temperature": 18}
        elif event.failure_type == "wot_malformed":
            device_states["thermostat"] = {"target_temperature": "NOT_A_NUMBER"}
    if page_state:
        tree["page_state"] = page_state
    return Observation(
        screenshot=observation.screenshot,
        dom_tree=observation.dom_tree,
        accessibility_tree=tree,
        wot_tds=observation.wot_tds,
        device_states=device_states,
        execution_history=list(observation.execution_history),
    )


def live_hook_for_event(event: ChaosEvent) -> dict[str, Any]:
    """Map abstract chaos events to existing React/node-wot fault hooks."""
    mapping = {
        "wot_timeout": {"surface": "wot", "type": "timeout", "thing": "thermostat", "delay_ms": 1500},
        "wot_offline": {"surface": "wot", "type": "offline", "thing": "projector"},
        "wot_malformed": {"surface": "wot", "type": "malformed", "thing": "thermostat"},
        "wot_postcondition_mismatch": {"surface": "wot", "type": "postcondition_mismatch", "thing": "thermostat"},
        "dom_selector_mutation": {"surface": "dom", "fault": "selector_mutation"},
        "dom_layout_shift": {"surface": "dom", "fault": "layout_shift"},
        "dom_stale_temperature": {"surface": "dom", "fault": "stale_temperature"},
        "dom_disabled_button": {"surface": "dom", "fault": "disabled_button"},
    }
    return dict(mapping.get(event.failure_type, {"surface": event.target, "type": event.failure_type}))


def _failure_result(skill_call: SkillCall, backend: str, event: ChaosEvent) -> ExecutionResult:
    return ExecutionResult(
        skill_id=skill_call.skill_id,
        backend_used=backend,
        success=False,
        latency_ms=25.0 * event.severity,
        confidence=0.0,
        failure_reason=event.failure_type,
        raw_observation_delta={},
    )


def _stale_delta_for(skill_call: SkillCall, observation: Observation) -> dict[str, Any]:
    if skill_call.skill_id == "set_temperature":
        current = (observation.device_states.get("thermostat_A") or {}).get("targetTemperature", 20)
        return {"thermostat_A": {"targetTemperature": current}}
    if skill_call.skill_id == "confirm_booking":
        return {"booking_status": "pending", "booking_confirmed": False}
    return {}
