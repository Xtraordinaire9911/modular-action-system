"""Adapters for feeding live DOM/WoT/Visual observations into runtime control.

The runtime loop should start from what the environment exposes now: page
affordances, device state, and optional visual marks. This module keeps that
boundary explicit so demos do not have to hand-populate ``CognitiveMap`` fields
in scattered places.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.contracts.types import Affordance, Observation
from src.perception.page_affordance_model import PageAffordanceModel
from src.runtime.cognitive_map import CognitiveMap


@dataclass(frozen=True)
class LiveRuntimeObservation:
    """One sanitized environment scan ready for ``ContinuousInteractionManager``."""

    observation: Observation
    affordances: list[Affordance] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    complete_affordance_snapshot: bool = True

    def apply_to(self, cognitive_map: CognitiveMap) -> Observation:
        """Update a map with observed affordances/state and return the observation."""

        if self.complete_affordance_snapshot:
            cognitive_map.replace_affordances(self.affordances)
        elif self.affordances:
            cognitive_map.update_affordances(self.affordances)
        cognitive_map.update_from_observation(self.observation)
        return self.observation

    def apply_affordances_to(self, cognitive_map: CognitiveMap) -> Observation:
        """Install affordances while leaving state ingestion to the episode loop."""

        if self.complete_affordance_snapshot:
            cognitive_map.replace_affordances(self.affordances)
        elif self.affordances:
            cognitive_map.update_affordances(self.affordances)
        return self.observation


def observation_from_live_sources(
    *,
    page: PageAffordanceModel | None = None,
    wot_affordances: list[Affordance] | None = None,
    visual_affordances: list[Affordance] | None = None,
    device_states: dict[str, Any] | None = None,
    page_state: dict[str, Any] | None = None,
    visual_state: dict[str, Any] | None = None,
    screenshot: bytes | None = None,
    wot_tds: list[dict[str, Any]] | None = None,
) -> LiveRuntimeObservation:
    """Build a live runtime observation from already-parsed environment outputs.

    DOM/WoT/Visual parsers remain owned by their modules. This adapter only
    consumes their typed outputs and makes the runtime-control input uniform.
    """

    affordances: list[Affordance] = []
    provenance: dict[str, Any] = {}
    normalized_page_state = dict(page_state or {})
    normalized_visual_state = dict(visual_state or {})

    if page is not None:
        affordances.extend(page.affordances)
        provenance["page_id"] = page.page_id
        provenance["url"] = page.url
        provenance["captured_at_ms"] = page.captured_at_ms
        normalized_page_state.setdefault(
            "page",
            {
                "page_id": page.page_id,
                "url": page.url,
                "affordance_count": len(page.affordances),
            },
        )
    if wot_affordances:
        affordances.extend(wot_affordances)
        provenance["wot_affordance_count"] = len(wot_affordances)
    if visual_affordances:
        affordances.extend(visual_affordances)
        provenance["visual_affordance_count"] = len(visual_affordances)
        normalized_visual_state.setdefault("visual_grounding", {"affordance_count": len(visual_affordances)})

    accessibility_tree: dict[str, Any] = {}
    if normalized_page_state:
        accessibility_tree["page_state"] = normalized_page_state
    if normalized_visual_state:
        accessibility_tree["visual_state"] = normalized_visual_state

    return LiveRuntimeObservation(
        observation=Observation(
            screenshot=screenshot,
            accessibility_tree=accessibility_tree or None,
            wot_tds=wot_tds,
            device_states=dict(device_states or {}),
        ),
        affordances=affordances,
        provenance=provenance,
    )
