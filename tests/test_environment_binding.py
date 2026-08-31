"""The bridge from an utterance's goal to something the runtime can check.

The failures this guards against are the ones that made the integration look
finished when it was not: a goal that resolves to the wrong control, a
parameter the runtime cannot ground, and a goal state that is not expressible
as a predicate - each of which the runtime correctly refuses.
"""

from __future__ import annotations

from src.planner.device_binding import composite_goal_for
from src.planner.environment_binding import BINDINGS, DEVICE_VIEWS, binding_for, device_view_for


def test_a_phrase_resolves_to_the_hook_the_page_uses():
    """The speaker says "wireless headphones"; the page's hook is "headphones"."""
    binding = binding_for("item_in_cart")

    assert binding.subject_of({"item": "wireless headphones"}) == "headphones"
    assert binding.subject_of({"item": "the Pro Laptop"}) == "laptop"
    assert binding.subject_of({"item": "keyboard"}) == "keyboard"


def test_an_unnamed_subject_resolves_to_nothing_rather_than_a_guess():
    binding = binding_for("item_in_cart")

    assert binding.subject_of({}) == ""
    assert binding.completion_for({}) == "", "no subject must mean no target, not the first one"


def test_the_completion_names_one_control_per_subject():
    binding = binding_for("item_in_cart")
    targets = {binding.completion_for({"item": item}) for item in ("headphones", "laptop", "keyboard", "monitor")}

    assert len(targets) == 4, f"a family of controls collapsed to one: {targets}"
    assert binding.completion_for({"item": "wireless headphones"}) == "button.add-cart-btn[data-id='headphones']"


def test_only_parameters_the_environment_can_enter_reach_the_runtime():
    """The runtime refuses to plan for a parameter it cannot ground, correctly.

    In this shop the item chooses which button completes the goal; it is not a
    value typed into a control. Forwarding it would make the runtime plan a
    type step whose postcondition nothing can observe.
    """
    binding = binding_for("item_in_cart")

    assert binding.bindings_for({"item": "headphones"}) == {}
    assert binding.runtime_parameters({"item": "headphones"}) == {}


def test_the_goal_is_expressed_as_a_predicate_the_runtime_can_evaluate():
    """ "item_in_cart" is the intent vocabulary; the runtime checks predicates."""
    binding = binding_for("item_in_cart")

    assert binding.runtime_goal_state() == "cart.holds_item == true"
    assert binding.observed_fact(True) == {"cart": {"holds_item": True}}
    assert binding.observed_fact(False) == {"cart": {"holds_item": False}}


def test_success_is_checked_in_the_region_the_goal_names():
    """A body-text check passes before the agent acts: the title is in the listing."""
    binding = binding_for("item_in_cart")

    assert binding.success_region({"item": "wireless headphones"}) == "#cart-items"
    assert binding.success_for({"item": "wireless headphones"}) == "headphones"


def test_upvote_success_requires_the_post_action_voted_state():
    binding = binding_for("post_upvoted")

    assert binding.success_region({"subject": "top"}) == "button.upvote-btn[data-post='1'].voted"
    assert binding.success_for({"subject": "top"}) == "▲"
    assert binding.success_for({"subject": "top"}), "an empty expected string matches every initial page state"


def test_every_binding_is_complete_enough_to_run():
    for goal_state, binding in BINDINGS.items():
        assert binding.goal_state == goal_state
        assert binding.state_entity and binding.state_attribute, f"{goal_state} has no checkable predicate"

        # A mock environment is a file the runner serves itself; the smart-room
        # dashboard is served by Docker. Naming a page file for the dashboard
        # would send a runner looking for a file that does not exist.
        if binding.surface == "mock_env":
            assert binding.page.endswith(".html"), f"{goal_state} names no page a runner can serve"
        else:
            assert not binding.page, f"{goal_state} is served by the {binding.surface}, so a page file misleads"

        # What the goal is about has to reach the environment somehow: either it
        # picks which control completes the goal (one add-to-cart button per
        # product), or it is typed into a control before one is pressed (one Book
        # Room button, and the room goes in the form). A binding doing neither
        # would act on whatever the page happened to be showing.
        grounded = "{subject}" in binding.completion_template or bool(binding.parameter_controls)
        assert grounded, f"{goal_state} never uses what the goal is about"


def test_a_goal_state_with_no_environment_is_reported_not_approximated():
    assert binding_for("projector_on") is None, "no entry must mean no attempt"


# ── what the visual channel is pointed at ───────────────────────────────────────
#
# A DeviceView says which rectangle of the dashboard settles a device goal. The
# choice of *which* reading to look at is the project's central argument, so it
# is asserted here rather than left to a comment.


def test_a_device_with_a_measured_reading_is_verified_against_the_measurement():
    """Not against the setpoint, which is free.

    Target changes the instant the write lands, so confirming it proves the
    thermostat was told. A jammed blinds motor reports its commanded position
    perfectly and never moves. Both views therefore read the slow half.
    """
    thermostat = device_view_for("temperature_set")
    assert thermostat.value_selector == "[data-testid='current-temp']"
    assert "target-temp" not in thermostat.value_selector

    blinds = device_view_for("blinds_set")
    assert blinds.value_selector == "[data-testid='blinds-measured']"
    assert "blinds-position" not in blinds.value_selector


def test_a_device_with_no_measured_counterpart_reads_the_only_value_it_has():
    """A dimmer really is instant, so inventing a measured brightness would be
    modelling a delay that does not exist."""
    lighting = device_view_for("lighting_set")

    assert lighting.value_selector == "[data-testid='brightness']"


def test_every_view_is_addressed_by_test_id_rather_than_by_layout():
    """The dashboard's styling is expected to change; its test ids are a contract.

    A view pinned to a class name or a position would break on a redesign and
    the failure would look like a planner bug.
    """
    for goal_state, view in DEVICE_VIEWS.items():
        assert view.region.startswith("[data-testid="), goal_state
        assert view.value_selector.startswith("[data-testid="), goal_state
        # The value must live inside the region, or the crop handed to the model
        # would not contain the reading the claim is about.
        assert view.region != view.value_selector, goal_state


def test_every_writable_part_of_a_prepared_room_can_also_be_seen():
    """The visual channel has to cover what the composite goal writes.

    Blinds were the gap: the room wrote a position that no panel displayed, so
    the one device whose commanded and measured values can disagree the longest
    was the one the vision model could not be asked about.
    """
    for part in composite_goal_for("room_prepared").parts:
        assert device_view_for(part.goal_state) is not None, part.goal_state


def test_a_claim_is_answerable_from_the_crop_alone():
    """Each question names the reading and the value, so a model that cannot see
    the panel has no way to answer it correctly by guessing context."""
    for goal_state, view in DEVICE_VIEWS.items():
        question = view.question_for(22)
        assert "22" in question, goal_state
        assert question.endswith("Answer from the image only."), goal_state
