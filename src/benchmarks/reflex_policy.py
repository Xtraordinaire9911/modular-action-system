"""A tiny goal-driven System-1 policy for visibly running on external pages.

This is deliberately not an LLM planner: it is the deterministic reflex layer
that lets the visual runner pick a sensible next affordance from a Page
Affordance Model so a person can *watch* the agent fill inputs and click on a
real benchmark page. The full System-2 planner remains a separate concern.
"""

from __future__ import annotations

from typing import Any

from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel


def select_next(
    pam: PageAffordanceModel,
    goal: str,
    *,
    values: dict[str, Any] | None = None,
    used_ids: tuple[str, ...] = (),
) -> tuple[Affordance, Any] | None:
    """Pick the next (affordance, value) to act on, or None when nothing is left.

    Priority: (1) fill an input that has a caller-provided value, (2) click the
    button whose label best overlaps the goal text, (3) any not-yet-used
    clickable. ``used_ids`` is threaded by the caller to avoid re-acting on the
    same element and looping forever.
    """
    values = values or {}
    used = set(used_ids)

    # (1) Type into inputs for which we were given a value (match by label, case-insensitive).
    lowered = {k.strip().lower(): v for k, v in values.items()}
    for aff in pam.inputs():
        if aff.id in used:
            continue
        key = aff.label.strip().lower()
        if key in lowered:
            return aff, lowered[key]

    # Bare <label> nodes (id "dom_label_*") are page chrome/status text, not real
    # click targets; clicking them just hangs actionability. Skip them here.
    clickable = [a for a in pam.clickable() if not a.id.startswith("dom_label_")]

    # (2) Click the goal-relevant button (token overlap with the goal text).
    goal_tokens = {t for t in goal.lower().split() if t}
    best: Affordance | None = None
    best_score = 0
    for aff in clickable:
        if aff.id in used:
            continue
        score = len(goal_tokens & {t for t in aff.label.lower().split() if t})
        if score > best_score:
            best, best_score = aff, score
    if best is not None:
        return best, None

    # (3) Fall back to the first unused clickable.
    for aff in clickable:
        if aff.id not in used:
            return aff, None
    return None
