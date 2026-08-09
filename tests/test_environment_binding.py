"""The bridge from an utterance's goal to something the runtime can check.

The failures this guards against are the ones that made the integration look
finished when it was not: a goal that resolves to the wrong control, a
parameter the runtime cannot ground, and a goal state that is not expressible
as a predicate - each of which the runtime correctly refuses.
"""

from __future__ import annotations

from src.planner.environment_binding import BINDINGS, binding_for


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


def test_every_binding_is_complete_enough_to_run():
    for goal_state, binding in BINDINGS.items():
        assert binding.goal_state == goal_state
        assert binding.page.endswith(".html")
        assert "{subject}" in binding.completion_template
        assert binding.state_entity and binding.state_attribute, f"{goal_state} has no checkable predicate"


def test_a_goal_state_with_no_environment_is_reported_not_approximated():
    assert binding_for("projector_on") is None, "no entry must mean no attempt"
