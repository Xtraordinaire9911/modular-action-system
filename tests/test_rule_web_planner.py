"""Tests for the rule-first external web planner."""

from __future__ import annotations

import pytest

from src.benchmarks.rule_web_planner import LLMWebPlanner, RuleBasedWebPlanner, WebPlannerHistory
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


def _pam(*affordances: Affordance) -> PageAffordanceModel:
    return PageAffordanceModel("page", "https://example.test", list(affordances))


def test_rule_planner_fills_login_inputs_then_clicks_login():
    planner = RuleBasedWebPlanner()
    pam = _pam(
        _aff("user", "Username", "type"),
        _aff("pw", "Password", "type"),
        _aff("login", "Login"),
    )
    values = {"Username": "standard_user", "Password": "secret_sauce"}

    first = planner.next_action(pam, "login", values=values)
    history = WebPlannerHistory().append(first)
    second = planner.next_action(pam, "login", values=values, history=history)
    history = history.append(second)
    third = planner.next_action(pam, "login", values=values, history=history)

    assert first.affordance and first.affordance.label == "Username"
    assert first.value == "standard_user"
    assert second.affordance and second.affordance.label == "Password"
    assert second.value == "secret_sauce"
    assert third.affordance and third.affordance.label == "Login"
    assert third.value is None


def test_rule_planner_moves_from_product_to_cart_and_checkout():
    planner = RuleBasedWebPlanner()
    values = {"product": "backpack"}

    product = planner.next_action(
        _pam(
            _aff("cart", "shopping cart", selector="a.shopping_cart_link"),
            _aff("add_backpack", "add-to-cart-sauce-labs-backpack"),
            _aff("add_bike", "add-to-cart-sauce-labs-bike-light"),
        ),
        "buy backpack and checkout",
        values=values,
    )
    cart = planner.next_action(
        _pam(_aff("cart", "1", selector="a.shopping_cart_link")),
        "buy backpack and checkout",
        values=values,
        history=WebPlannerHistory().append(product),
    )
    checkout = planner.next_action(
        _pam(_aff("checkout", "checkout")),
        "buy backpack and checkout",
        values=values,
    )

    assert product.affordance and product.affordance.id == "add_backpack"
    assert cart.affordance and cart.affordance.id == "cart"
    assert checkout.affordance and checkout.affordance.id == "checkout"


def test_rule_planner_does_not_bind_product_value_to_sort_select_or_unmatched_products():
    planner = RuleBasedWebPlanner()
    values = {"product": "backpack"}

    select_decision = planner.next_action(
        _pam(_aff("sort", "select", "select", selector="select.product_sort_container")),
        "buy backpack and checkout",
        values=values,
    )
    unmatched_add = planner.next_action(
        _pam(_aff("add_bike", "add-to-cart-sauce-labs-bike-light")),
        "buy backpack and checkout",
        values=values,
    )

    assert select_decision.done is True
    assert select_decision.affordance is None
    assert unmatched_add.done is True
    assert unmatched_add.affordance is None


def test_rule_planner_fills_checkout_info_and_finishes_order():
    planner = RuleBasedWebPlanner()
    values = {"first_name": "Yixin", "last_name": "Yang", "postal_code": "80333"}
    pam = _pam(
        _aff("first", "First Name", "type"),
        _aff("last", "Last Name", "type"),
        _aff("zip", "Zip/Postal Code", "type"),
        _aff("continue", "Continue"),
    )

    first = planner.next_action(pam, "complete checkout", values=values)
    history = WebPlannerHistory().append(first)
    last = planner.next_action(pam, "complete checkout", values=values, history=history)
    history = history.append(last)
    zip_code = planner.next_action(pam, "complete checkout", values=values, history=history)
    history = history.append(zip_code)
    continue_action = planner.next_action(pam, "complete checkout", values=values, history=history)
    finish = planner.next_action(_pam(_aff("finish", "Finish")), "complete checkout", values=values)

    assert first.affordance and first.affordance.id == "first"
    assert last.affordance and last.affordance.id == "last"
    assert zip_code.affordance and zip_code.affordance.id == "zip"
    assert continue_action.affordance and continue_action.affordance.id == "continue"
    assert finish.affordance and finish.affordance.id == "finish"


def test_llm_planner_is_explicitly_reserved_for_future_work():
    with pytest.raises(NotImplementedError):
        LLMWebPlanner().next_action(_pam(), "buy backpack")
