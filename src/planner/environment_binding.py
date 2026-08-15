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
    page: str  # file under env/mock_envs, or "" when the surface is the dashboard
    completion_template: str  # selector family; {subject} filled from parameters
    # Which parameter names the thing acted on. A tuple because the intent layer
    # is free to name it: the rule fallback emits "item"/"subject", and a model
    # reading the same prompt may reasonably emit "target" or "post". Binding to
    # one name made every model-derived forum goal unsupported.
    subject_parameter: str
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
    # Other names the same value may arrive under, tried in order after the
    # primary one. Naming is the intent layer's business, not this layer's.
    subject_parameter_aliases: tuple[str, ...] = ()
    # What the verification region *looks like* once the goal holds, phrased so a
    # model shown only that crop can answer it. This has to be declared per
    # binding rather than derived from the subject: the cart region shows the
    # item's name, while the upvote region is a 32x32 arrow whose only visible
    # change is that it becomes filled. Asking about the post title against an
    # arrow got a confident False, correctly - the crop had no title in it.
    visual_claim: str = "an entry for {subject}"
    # Which surface serves this goal. The mock environments are files a runner
    # can serve itself; the smart-room dashboard is served by Docker on a fixed
    # port and is the *digital half* of the declared use case, sitting over the
    # same devices the WoT side writes to. A runner that cannot reach a surface
    # must say so rather than fetching a 404 and reporting a missing control.
    surface: str = "mock_env"  # "mock_env" | "dashboard"

    def subject_of(self, parameters: dict[str, Any]) -> str:
        """The concrete thing this goal is about, as the page names it.

        The utterance says "wireless headphones"; the page's hook is
        ``headphones``. Matching is on the last word so a phrase, a shortened
        form, or a change of adjective all still resolve - and an unknown
        subject returns empty rather than a guess, so the caller can refuse.
        """
        raw = self.raw_subject(parameters).lower()
        if not raw:
            return ""
        if raw in self.subject_aliases:
            return self.subject_aliases[raw]
        for phrase, hook in self.subject_aliases.items():
            if phrase in raw or raw in phrase:
                return hook
        tail = re.sub(r"[^a-z0-9 ]", "", raw).split()
        return tail[-1] if tail else ""

    def raw_subject(self, parameters: dict[str, Any]) -> str:
        """The subject as the person said it, under whichever name it arrived.

        Kept separate from :meth:`subject_of` because the two have different
        readers: the page wants its own hook ("monitor"), and a question put to a
        vision model wants the words a person would use ("4K Monitor"). Asking
        the model about the internal hook produced confident wrong answers.
        """
        for name in (self.subject_parameter, *self.subject_parameter_aliases):
            value = str(parameters.get(name, "")).strip()
            if value:
                return value
        return ""

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

    def visual_question(self, parameters: dict[str, Any]) -> str:
        """One question about the verification region, answerable from it alone."""
        claim = self.visual_claim.format(subject=self.raw_subject(parameters) or "the expected item")
        return f"Does this image show {claim}? Answer from the image only."

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
    "room_booked": EnvironmentBinding(
        goal_state="room_booked",
        page="",  # the dashboard is served by Docker, not by the runner
        surface="dashboard",
        completion_template="[data-testid='book-room-button']",
        subject_parameter="room",
        subject_parameter_aliases=("target", "subject", "name"),
        success_selector="[data-testid='booking-status']",
        # Not just "booked". Before anything is clicked the same element reads
        # "not booked", which *contains* that word, so a bare check reports the
        # goal as already met and the episode passes without acting. Requiring
        # the room the model named makes the proof both unambiguous and
        # dependent on the model's own answer.
        success_template="booked: room {subject}",
        state_entity="room",
        state_attribute="booked",
        # The room and the time are typed into the form before the button is
        # pressed, so what the model extracted is what actually gets booked. A
        # run that clicked Book Room without filling these would book whatever
        # the form happened to be showing and still report success.
        parameter_controls={
            "room": "[data-testid='room-input']",
            "time": "[data-testid='time-input']",
        },
        visual_claim="a booking confirmation naming room {subject}",
    ),
    "item_in_cart": EnvironmentBinding(
        goal_state="item_in_cart",
        page="shopping.html",
        completion_template="button.add-cart-btn[data-id='{subject}']",
        subject_parameter="item",
        subject_parameter_aliases=("target", "product", "subject"),
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
        subject_parameter_aliases=("target", "post", "item", "title"),
        # The whole visible change is the button turning from outlined to filled.
        visual_claim="a small triangular vote button that is filled in with a solid colour rather than plain or outlined",
        # A vote count is already non-empty before the action.  Scope the oracle
        # to the state-bearing class the page adds only after a successful vote;
        # otherwise an empty success string matches the initial count and the
        # episode is falsely reported as solved without a transition.
        success_selector="button.upvote-btn[data-post='{subject}'].voted",
        success_template="▲",
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


# --- the physical half -----------------------------------------------------------
# A device goal is not completed by clicking anything on a page: the write target
# is resolved from the Thing Descriptions the room publishes (see
# src.planner.device_binding) and the value goes over WoT. But the dashboard is a
# *view* of those same devices, so the effect becomes visible there a moment
# later, and that is where a person - or a vision model - can confirm it.
#
# Keeping this separate from EnvironmentBinding is deliberate. The tables above
# say which control completes a goal; there is no such control here, and giving a
# device goal an empty completion would invite a runner to click nothing and call
# it done. This says only where to look afterwards.


@dataclass(frozen=True)
class DeviceView:
    """Where a device goal becomes visible on the smart-room dashboard."""

    goal_state: str
    region: str  # the panel to crop for the vision model
    value_selector: str  # the element whose text carries the value
    suffix: str = ""  # what the dashboard prints after the value, e.g. " C"
    visual_claim: str = ""  # asked of a crop of `region`, answerable from it alone

    def proof_for(self, value: Any) -> str:
        """The text that must appear once the device really holds ``value``.

        Numbers arrive from a servient as ``22`` or ``22.0`` and the dashboard
        renders whichever it was given, so an integral float is compared as the
        integer a person would read.
        """
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{value}{self.suffix}"

    def question_for(self, value: Any) -> str:
        claim = (self.visual_claim or "the value {value}").format(value=self.proof_for(value))
        return f"Does this image show {claim}? Answer from the image only."


DEVICE_VIEWS: dict[str, DeviceView] = {
    "temperature_set": DeviceView(
        goal_state="temperature_set",
        region="[data-testid='thermostat-panel']",
        value_selector="[data-testid='target-temp']",
        suffix=" C",
        visual_claim="a thermostat panel whose Target reads {value}",
    ),
    "lighting_set": DeviceView(
        goal_state="lighting_set",
        region="[data-testid='lighting-panel']",
        value_selector="[data-testid='brightness']",
        suffix=" %",
        visual_claim="a lighting panel whose Brightness reads {value}",
    ),
    "projector_on": DeviceView(
        goal_state="projector_on",
        region="[data-testid='projector-panel']",
        value_selector="[data-testid='projector-power']",
        visual_claim="a projector panel whose Power reads {value}",
    ),
    "projector_off": DeviceView(
        goal_state="projector_off",
        region="[data-testid='projector-panel']",
        value_selector="[data-testid='projector-power']",
        visual_claim="a projector panel whose Power reads {value}",
    ),
}


def device_view_for(goal_state: str) -> DeviceView | None:
    return DEVICE_VIEWS.get(goal_state)


__all__ = [
    "BINDINGS",
    "DEVICE_VIEWS",
    "DeviceView",
    "EnvironmentBinding",
    "binding_for",
    "device_view_for",
]
