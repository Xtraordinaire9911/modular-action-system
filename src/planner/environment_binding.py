"""Which environment can satisfy a goal state, and which control completes it.

An utterance says what the person wants. It cannot say which page to open or
which button ends the task, because the speaker does not know and should not
have to. Something has to connect the two, and the honest thing is to declare
that connection in one readable place rather than let it hide inside a runner.

That is what this is: a small table from a ``GoalSpec.goal_state`` to the
environment that can satisfy it, the affordance whose use completes it, and the
text that proves it happened. The runtime consumes exactly these three things -
``completions`` marks an affordance as achieving the goal, ``bindings`` attaches
a parameter to a control, and ``success_text`` is checked by re-observing.

What this deliberately does **not** do is choose the target. The completion
entry is a family of controls ("any add-to-cart button"), and which member is
right for *this* goal comes from the parameter the intent layer extracted. A
table that named one button per utterance would be the hardcoded skill map the
review told us to remove.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EnvironmentBinding:
    """Everything the runtime needs to attempt one goal state in one environment."""

    goal_state: str
    page: str  # file under env/mock_envs
    completion_template: str  # selector family; {subject} filled from parameters
    subject_parameter: str  # which parameter names the thing acted on
    success_selector: str  # the region whose text proves the goal
    success_template: str  # text that must appear there, {subject} filled the same way
    # The intent layer speaks in goal names ("item_in_cart"); the runtime checks
    # predicates against observed state ("cart.holds_item == true"). Translating
    # between the two is this layer's job - the runtime is right to refuse a
    # goal it cannot express as something checkable.
    state_entity: str = ""
    state_attribute: str = ""
    # Which control accepts each goal parameter as a value. The runtime plans a
    # "bind_parameter" step per entry and refuses to plan for a parameter it
    # cannot ground, which is correct: a goal naming something the environment
    # cannot point at is not actionable.
    parameter_controls: dict[str, str] = field(default_factory=dict)
    subject_aliases: dict[str, str] = field(default_factory=dict)

    def subject_of(self, parameters: dict[str, Any]) -> str:
        """The concrete thing this goal is about, as the page names it.

        The utterance says "wireless headphones"; the page's hook is
        ``headphones``. Matching is on the last word so a phrase, a shortened
        form, or a change of adjective all still resolve - and an unknown
        subject returns empty rather than a guess, so the caller can refuse.
        """
        raw = str(parameters.get(self.subject_parameter, "")).strip().lower()
        if not raw:
            return ""
        if raw in self.subject_aliases:
            return self.subject_aliases[raw]
        for phrase, hook in self.subject_aliases.items():
            if phrase in raw or raw in phrase:
                return hook
        tail = re.sub(r"[^a-z0-9 ]", "", raw).split()
        return tail[-1] if tail else ""

    def completion_for(self, parameters: dict[str, Any]) -> str:
        """The selector whose use completes this goal, or empty if unresolved."""
        subject = self.subject_of(parameters)
        return self.completion_template.format(subject=subject) if subject else ""

    def success_for(self, parameters: dict[str, Any]) -> str:
        """Text that must appear in :attr:`success_selector` once the goal is met."""
        subject = self.subject_of(parameters)
        return self.success_template.format(subject=subject) if subject else ""

    def success_region(self, parameters: dict[str, Any]) -> str:
        subject = self.subject_of(parameters)
        return self.success_selector.format(subject=subject) if subject else ""

    def runtime_goal_state(self) -> str:
        """The goal as a predicate the runtime's condition evaluator can check."""
        return f"{self.state_entity}.{self.state_attribute} == true"

    def observed_fact(self, satisfied: bool) -> dict[str, dict[str, Any]]:
        """The page-state entry that makes the predicate resolvable."""
        return {self.state_entity: {self.state_attribute: satisfied}}

    def bindings_for(self, parameters: dict[str, Any]) -> dict[str, str]:
        """Which control carries each goal parameter, for the parameters we have."""
        return {name: control for name, control in self.parameter_controls.items() if name in parameters}

    def runtime_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """The parameters the runtime can actually ground, and only those.

        A parameter with no control is not dropped silently anywhere else: it
        has already done its work here, choosing which completion applies.
        Forwarding it as well would make the runtime refuse to plan for a value
        it has no way to enter.
        """
        return {name: value for name, value in parameters.items() if name in self.parameter_controls}


# One entry per goal state this repository can actually attempt. A goal state
# with no entry is reported as unsupported here rather than attempted badly.
BINDINGS: dict[str, EnvironmentBinding] = {
    "item_in_cart": EnvironmentBinding(
        goal_state="item_in_cart",
        page="shopping.html",
        completion_template="button.add-cart-btn[data-id='{subject}']",
        subject_parameter="item",
        # The cart, not the whole page. The product name is printed in the
        # listing before anything is added, so a body-text check reports the
        # goal as already met and the episode passes without acting.
        success_selector="#cart-items",
        success_template="{subject}",
        state_entity="cart",
        state_attribute="holds_item",
        # No parameter is typed here. This shop gives every product its own
        # add-to-cart button, so the item chooses *which* control completes the
        # goal rather than being a value entered into one. Declaring it as a
        # bound parameter would make the runtime plan a type step whose
        # postcondition nothing can observe.
        parameter_controls={},
        subject_aliases={
            "wireless headphones": "headphones",
            "pro laptop": "laptop",
            "mechanical keyboard": "keyboard",
            "4k monitor": "monitor",
        },
    ),
    "post_upvoted": EnvironmentBinding(
        goal_state="post_upvoted",
        page="forum.html",
        completion_template="button.upvote-btn[data-post='{subject}']",
        subject_parameter="subject",
        success_selector="#votes-{subject}",
        success_template="",
        state_entity="post",
        state_attribute="upvoted",
        subject_aliases={
            "ai agents": "1",
            "top": "1",
            "browser automation": "2",
            "programming paradigms": "3",
        },
    ),
}


def binding_for(goal_state: str) -> EnvironmentBinding | None:
    return BINDINGS.get(goal_state)


__all__ = ["BINDINGS", "EnvironmentBinding", "binding_for"]
