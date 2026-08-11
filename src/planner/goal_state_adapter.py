"""Report whether the goal state holds, so the runtime can verify it.

The runtime checks a primitive's expected effect by looking the goal state up as
a condition path in what it just observed. `RuntimeWebEnvironmentAdapter`
reports the page's affordances and a `benchmark.solved` flag, and nothing under
the goal state's own name - so the lookup misses and the episode ends

    postcondition_failed: expected_effect='item_in_cart', observed=None,
    reason='missing condition path: item_in_cart'

even when the cart demonstrably contains the item. That is a false negative, and
it is the exact mirror of the false success this project exists to prevent: the
verifier is right to refuse, because nothing told it what to look at.

This wraps any runtime adapter and adds one fact to each observation - whether
the goal state currently holds, as judged by re-reading the environment. The
check is supplied by the caller and must observe the world rather than trust
that an action ran; passing one that reports the executor's own success would
reintroduce the problem it is here to fix.

Composed rather than patched: the wrapped adapter is unchanged and keeps
working for every existing caller.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Protocol


class _RuntimeAdapter(Protocol):
    async def reset(self, spec: Any) -> None: ...

    async def observe(self, request: Any) -> Any: ...

    def executors(self) -> dict[str, Any]: ...


class GoalStateReportingAdapter:
    """A runtime adapter that also reports whether the goal state holds."""

    def __init__(
        self, inner: _RuntimeAdapter, *, fact: Callable[[bool], dict[str, Any]], holds: Callable[[], bool]
    ) -> None:
        self._inner = inner
        self._fact = fact
        self._holds = holds
        self.observations: list[bool] = []

    async def reset(self, spec: Any) -> None:
        await self._inner.reset(spec)

    def executors(self) -> dict[str, Any]:
        return self._inner.executors()

    async def observe(self, request: Any) -> Any:
        live = await self._inner.observe(request)
        satisfied = bool(self._holds())
        self.observations.append(satisfied)

        fact = self._fact(satisfied)
        observation = getattr(live, "observation", None)
        if observation is None:  # a plain Observation, not a live wrapper
            return _with_fact(live, fact)
        return replace(live, observation=_with_fact(observation, fact))


def _with_fact(observation: Any, fact: dict[str, Any]) -> Any:
    """Merge the fact into the observed page state, leaving the rest alone.

    The runtime reads page state out of the accessibility tree, which is where
    its own ingestion looks; writing it anywhere else would be recorded but
    never consulted.
    """
    tree = dict(getattr(observation, "accessibility_tree", {}) or {})
    page_state = dict(tree.get("page_state") or {})
    for entity, values in fact.items():
        merged = dict(page_state.get(entity) or {})
        merged.update(values)
        page_state[entity] = merged
    tree["page_state"] = page_state
    return replace(observation, accessibility_tree=tree)


__all__ = ["GoalStateReportingAdapter"]
