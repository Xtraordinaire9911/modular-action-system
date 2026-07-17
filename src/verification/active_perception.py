"""Active perception loop for resolving blocking observation conflicts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.contracts.types import Observation
from src.runtime.cognitive_map import CognitiveMap, Conflict
from src.verification.conflict_detector import EpistemicArbiter


class ActivePerceptionProbe(Protocol):
    async def observe(
        self,
        conflicts: list[Conflict],
        cognitive_map: CognitiveMap,
        original_observation: Observation,
    ) -> Observation | None:
        """Return a fresh observation, or None if re-observation failed."""


@dataclass(frozen=True)
class ActivePerceptionResult:
    resolved: bool
    trace: list[dict[str, Any]] = field(default_factory=list)
    remaining_conflict_ids: list[str] = field(default_factory=list)


class ActivePerceptionResolver:
    """Try fresh observations before escalating a blocking sensory conflict."""

    def __init__(
        self,
        probe: ActivePerceptionProbe,
        *,
        max_attempts: int = 1,
        arbiter: EpistemicArbiter | None = None,
    ) -> None:
        self.probe = probe
        self.max_attempts = max(1, max_attempts)
        self.arbiter = arbiter or EpistemicArbiter()

    async def resolve(
        self,
        conflicts: list[Conflict],
        cognitive_map: CognitiveMap,
        original_observation: Observation,
    ) -> ActivePerceptionResult:
        trace: list[dict[str, Any]] = []
        initial_ids = [conflict.id for conflict in conflicts]

        for attempt in range(1, self.max_attempts + 1):
            fresh = await self.probe.observe(conflicts, cognitive_map, original_observation)
            if fresh is None:
                trace.append(
                    {
                        "action": "active_perception_probe",
                        "attempt": attempt,
                        "resolved": False,
                        "reason": "probe returned no observation",
                    }
                )
                continue

            cognitive_map.update_from_observation(fresh)
            new_conflicts = self.arbiter.check(cognitive_map)
            blocking = [conflict for conflict in new_conflicts if conflict.conflict_mass >= self.arbiter.halt_threshold]
            if not blocking:
                cognitive_map.resolve_conflicts(initial_ids, decision="active_perception_resolved")
                trace.append(
                    {
                        "action": "active_perception_probe",
                        "attempt": attempt,
                        "resolved": True,
                        "reason": "fresh observation removed blocking conflict",
                    }
                )
                return ActivePerceptionResult(resolved=True, trace=trace)

            conflicts = blocking
            trace.append(
                {
                    "action": "active_perception_probe",
                    "attempt": attempt,
                    "resolved": False,
                    "remaining_conflict_ids": [conflict.id for conflict in blocking],
                }
            )

        remaining = [conflict.id for conflict in conflicts]
        return ActivePerceptionResult(resolved=False, trace=trace, remaining_conflict_ids=remaining)
