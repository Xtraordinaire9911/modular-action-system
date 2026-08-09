"""Tests for separated web task and affordance planning."""

from __future__ import annotations

import pytest

from src.benchmarks.rule_web_planner import LLMWebPlanner, RuleBasedAffordancePlanner, WebPlannerHistory
from src.benchmarks.web_task_planner import LLMWebTaskPlanner, RuleBasedWebTaskPlanner, WebSubgoal, subgoal_satisfied
from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel


def _aff(
    affordance_id: str,
    label: str,
    action: str = "click",
    *,
    selector: str = "",
    kind: str = "button",
) -> Affordance:
    return Affordance(
        id=affordance_id,
        source="DOM",
        type="input" if action in {"type", "select"} else kind,  # type: ignore[arg-type]
        label=label,
        action=action,
        locator={"selector": selector or f"#{affordance_id}"},
        confidence=0.95,
    )


def _pam(*affordances: Affordance, url: str = "https://example.test") -> PageAffordanceModel:
    return PageAffordanceModel("page", url, list(affordances))


def _subgoal(kind: str, **parameters: str) -> WebSubgoal:
    return WebSubgoal(id=kind, kind=kind, description=kind, parameters=parameters)  # type: ignore[arg-type]


def test_task_planner_decomposes_purchase_goal_into_inspectable_subgoals():
    plan = RuleBasedWebTaskPlanner().plan(
        "buy backpack and complete checkout",
        values={
            "Username": "standard_user",
            "Password": "secret_sauce",
            "Product": "backpack",
            "First Name": "Yixin",
            "Last Name": "Yang",
            "Zip/Postal Code": "80333",
        },
    )

    assert [subgoal.kind for subgoal in plan.subgoals] == [
        "login",
        "add_product",
        "open_cart",
        "checkout",
        "fill_checkout_info",
        "finish_order",
    ]
    assert plan.subgoals[1].parameters == {"product": "backpack"}


def test_task_planner_falls_back_to_generic_subgoal_for_unknown_goals():
    plan = RuleBasedWebTaskPlanner().plan("inspect page health")

    assert len(plan.subgoals) == 1
    assert plan.subgoals[0].kind == "generic_goal"


def test_affordance_planner_fills_login_inputs_then_clicks_login():
    planner = RuleBasedAffordancePlanner()
    subgoal = _subgoal("login")
    pam = _pam(
        _aff("user", "Username", "type"),
        _aff("pw", "Password", "type"),
        _aff("login", "Login"),
    )
    values = {"Username": "standard_user", "Password": "secret_sauce"}

    first = planner.next_action(pam, subgoal, values=values)
    history = WebPlannerHistory().append(first)
    second = planner.next_action(pam, subgoal, values=values, history=history)
    history = history.append(second)
    third = planner.next_action(pam, subgoal, values=values, history=history)

    assert first.affordance and first.affordance.label == "Username"
    assert first.value == "standard_user"
    assert second.affordance and second.affordance.label == "Password"
    assert second.value == "secret_sauce"
    assert third.affordance and third.affordance.label == "Login"
    assert third.value is None


def test_affordance_planner_moves_from_product_to_cart_and_checkout_by_subgoal():
    planner = RuleBasedAffordancePlanner()

    product = planner.next_action(
        _pam(
            _aff("cart", "shopping cart", selector="a.shopping_cart_link"),
            _aff("add_backpack", "add-to-cart-sauce-labs-backpack"),
            _aff("add_bike", "add-to-cart-sauce-labs-bike-light"),
        ),
        _subgoal("add_product", product="backpack"),
    )
    cart = planner.next_action(_pam(_aff("cart", "1", selector="a.shopping_cart_link")), _subgoal("open_cart"))
    checkout = planner.next_action(_pam(_aff("checkout", "checkout")), _subgoal("checkout"))

    assert product.affordance and product.affordance.id == "add_backpack"
    assert cart.affordance and cart.affordance.id == "cart"
    assert checkout.affordance and checkout.affordance.id == "checkout"


def test_affordance_planner_does_not_bind_product_value_to_sort_select_or_unmatched_products():
    planner = RuleBasedAffordancePlanner()

    select_decision = planner.next_action(
        _pam(_aff("sort", "select", "select", selector="select.product_sort_container")),
        _subgoal("add_product", product="backpack"),
    )
    unmatched_add = planner.next_action(
        _pam(_aff("add_bike", "add-to-cart-sauce-labs-bike-light")),
        _subgoal("add_product", product="backpack"),
    )

    assert select_decision.done is True
    assert select_decision.affordance is None
    assert unmatched_add.done is True
    assert unmatched_add.affordance is None


def test_affordance_planner_fills_checkout_info_and_finishes_order():
    planner = RuleBasedAffordancePlanner()
    values = {"first_name": "Yixin", "last_name": "Yang", "postal_code": "80333"}
    subgoal = _subgoal("fill_checkout_info")
    pam = _pam(
        _aff("first", "First Name", "type"),
        _aff("last", "Last Name", "type"),
        _aff("zip", "Zip/Postal Code", "type"),
        _aff("continue", "Continue"),
    )

    first = planner.next_action(pam, subgoal, values=values)
    history = WebPlannerHistory().append(first)
    last = planner.next_action(pam, subgoal, values=values, history=history)
    history = history.append(last)
    zip_code = planner.next_action(pam, subgoal, values=values, history=history)
    history = history.append(zip_code)
    continue_action = planner.next_action(pam, subgoal, values=values, history=history)
    finish = planner.next_action(_pam(_aff("finish", "Finish")), _subgoal("finish_order"), values=values)

    assert first.affordance and first.affordance.id == "first"
    assert last.affordance and last.affordance.id == "last"
    assert zip_code.affordance and zip_code.affordance.id == "zip"
    assert continue_action.affordance and continue_action.affordance.id == "continue"
    assert finish.affordance and finish.affordance.id == "finish"


def test_subgoal_progress_checks_are_observation_based():
    assert subgoal_satisfied(_pam(url="https://shop.test/inventory.html"), "Products", _subgoal("login"))
    assert subgoal_satisfied(_pam(url="https://shop.test/cart.html"), "Your Cart", _subgoal("open_cart"))
    assert subgoal_satisfied(
        _pam(url="https://shop.test/checkout-step-two.html"),
        "Checkout: Overview",
        _subgoal("fill_checkout_info"),
    )
    assert subgoal_satisfied(_pam(), "Thank you for your order", _subgoal("finish_order"), success_text=["Thank you"])


def test_llm_planners_are_explicitly_reserved_for_future_work():
    with pytest.raises(NotImplementedError):
        LLMWebTaskPlanner().plan("buy backpack")
    with pytest.raises(NotImplementedError):
        LLMWebPlanner().next_action(_pam(), _subgoal("generic_goal"))
