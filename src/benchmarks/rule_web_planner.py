"""Rule-first affordance planner over observed page affordances.

This module selects the next concrete ``Affordance`` for one active subgoal.
Task decomposition lives in ``web_task_planner``. The split keeps the runtime
loop honest: observe the current page, check the current subgoal, then choose
one action from the fresh Page Affordance Model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.benchmarks.web_task_planner import WebSubgoal, normalize_web_values, tokens
from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel


@dataclass(frozen=True)
class WebPlannerDecision:
    affordance: Affordance | None
    value: Any | None = None
    reason: str = ""
    done: bool = False
    page_url: str = ""
    subgoal_id: str = ""


@dataclass(frozen=True)
class WebPlannerHistory:
    steps: list[WebPlannerDecision] = field(default_factory=list)

    def append(self, decision: WebPlannerDecision) -> "WebPlannerHistory":
        return WebPlannerHistory([*self.steps, decision])


class WebAffordancePlanner(Protocol):
    def next_action(
        self,
        pam: PageAffordanceModel,
        subgoal: WebSubgoal,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision: ...


class RuleBasedAffordancePlanner:
    """Deterministic next-action planner for one current web subgoal."""

    def next_action(
        self,
        pam: PageAffordanceModel,
        subgoal: WebSubgoal,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision:
        values = normalize_web_values(values or {})
        history = history or WebPlannerHistory()

        if subgoal.kind in {"login", "fill_checkout_info", "generic_goal"}:
            input_decision = _next_unfilled_input(pam, subgoal, values, history)
            if input_decision is not None:
                return input_decision

        match subgoal.kind:
            case "login":
                return _login_action(pam, subgoal, values)
            case "add_product":
                return _add_to_cart_action(pam, subgoal)
            case "open_cart":
                return _open_cart_action(pam, subgoal)
            case "checkout":
                return _checkout_action(pam, subgoal)
            case "fill_checkout_info":
                return _continue_action(pam, subgoal)
            case "finish_order":
                return _finish_action(pam, subgoal)
            case "generic_goal":
                return _generic_action(pam, subgoal, values)

        return WebPlannerDecision(
            None,
            reason=f"unsupported subgoal kind: {subgoal.kind}",
            done=True,
            page_url=pam.url,
            subgoal_id=subgoal.id,
        )


class LLMWebPlanner:
    """Placeholder for a future LLM affordance planner."""

    def next_action(
        self,
        pam: PageAffordanceModel,
        subgoal: WebSubgoal,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision:
        _ = pam, subgoal, values, history
        raise NotImplementedError("LLM web planner is intentionally left empty; use RuleBasedAffordancePlanner for now")


RuleBasedWebPlanner = RuleBasedAffordancePlanner


def _next_unfilled_input(
    pam: PageAffordanceModel,
    subgoal: WebSubgoal,
    values: dict[str, Any],
    history: WebPlannerHistory,
) -> WebPlannerDecision | None:
    used_inputs = {
        decision.affordance.id
        for decision in history.steps
        if decision.affordance is not None
        and decision.affordance.action in {"type", "select"}
        and decision.page_url == pam.url
        and decision.subgoal_id == subgoal.id
    }
    for affordance in pam.inputs():
        if affordance.id in used_inputs:
            continue
        value = _value_for_input(affordance, values)
        if value is not None:
            return WebPlannerDecision(
                affordance,
                value=value,
                reason=f"fill observed input '{affordance.label}' for subgoal '{subgoal.id}'",
                page_url=pam.url,
                subgoal_id=subgoal.id,
            )
    return None


def _login_action(
    pam: PageAffordanceModel,
    subgoal: WebSubgoal,
    values: dict[str, Any],
) -> WebPlannerDecision:
    has_credentials = "username" in values and "password" in values
    if not has_credentials:
        return WebPlannerDecision(
            None, reason="missing login credentials", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    affordance = _first_clickable_matching(pam, {"login", "log-in", "sign-in", "submit"})
    if affordance is None:
        return WebPlannerDecision(
            None, reason="login button not observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    return WebPlannerDecision(affordance, reason="login form appears ready", page_url=pam.url, subgoal_id=subgoal.id)


def _add_to_cart_action(pam: PageAffordanceModel, subgoal: WebSubgoal) -> WebPlannerDecision:
    product_tokens = tokens(str(subgoal.parameters.get("product", "")))
    candidates = [aff for aff in pam.clickable() if {"add", "cart"} <= tokens(aff.label)]
    if not candidates:
        return WebPlannerDecision(
            None, reason="no add-to-cart affordance observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    if product_tokens:
        candidates.sort(key=lambda aff: len(product_tokens & tokens(aff.label)), reverse=True)
        if len(product_tokens & tokens(candidates[0].label)) == 0:
            return WebPlannerDecision(
                None,
                reason="no add-to-cart affordance matches requested product",
                done=True,
                page_url=pam.url,
                subgoal_id=subgoal.id,
            )
    return WebPlannerDecision(
        candidates[0], reason="add matching observed product to cart", page_url=pam.url, subgoal_id=subgoal.id
    )


def _open_cart_action(pam: PageAffordanceModel, subgoal: WebSubgoal) -> WebPlannerDecision:
    affordance = _first_cart_link(pam)
    if affordance is None:
        return WebPlannerDecision(
            None, reason="cart affordance not observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    return WebPlannerDecision(affordance, reason="open cart for checkout", page_url=pam.url, subgoal_id=subgoal.id)


def _checkout_action(pam: PageAffordanceModel, subgoal: WebSubgoal) -> WebPlannerDecision:
    affordance = _first_clickable_matching(pam, {"checkout"})
    if affordance is None:
        return WebPlannerDecision(
            None, reason="checkout affordance not observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    return WebPlannerDecision(affordance, reason="enter checkout flow", page_url=pam.url, subgoal_id=subgoal.id)


def _continue_action(pam: PageAffordanceModel, subgoal: WebSubgoal) -> WebPlannerDecision:
    affordance = _first_clickable_matching(pam, {"continue"})
    if affordance is None:
        return WebPlannerDecision(
            None, reason="continue affordance not observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    return WebPlannerDecision(
        affordance, reason="continue after checkout fields", page_url=pam.url, subgoal_id=subgoal.id
    )


def _finish_action(pam: PageAffordanceModel, subgoal: WebSubgoal) -> WebPlannerDecision:
    affordance = _first_clickable_matching(pam, {"finish", "place", "submit", "complete"})
    if affordance is None:
        return WebPlannerDecision(
            None, reason="finish affordance not observed", done=True, page_url=pam.url, subgoal_id=subgoal.id
        )
    return WebPlannerDecision(affordance, reason="finish final checkout step", page_url=pam.url, subgoal_id=subgoal.id)


def _generic_action(
    pam: PageAffordanceModel,
    subgoal: WebSubgoal,
    values: dict[str, Any],
) -> WebPlannerDecision:
    wanted = tokens(subgoal.description) | set(values)
    affordance = _first_clickable_matching(pam, wanted)
    if affordance is None:
        return WebPlannerDecision(
            None,
            reason="no generic affordance matched current subgoal",
            done=True,
            page_url=pam.url,
            subgoal_id=subgoal.id,
        )
    return WebPlannerDecision(
        affordance, reason="generic affordance matched current subgoal", page_url=pam.url, subgoal_id=subgoal.id
    )


def _value_for_input(affordance: Affordance, values: dict[str, Any]) -> Any | None:
    label_tokens = tokens(" ".join([affordance.id, affordance.label, str(affordance.locator.get("selector", ""))]))
    aliases = {
        "username": {"username", "user", "login"},
        "password": {"password", "pass"},
        "first_name": {"first", "firstname", "name"},
        "last_name": {"last", "lastname", "name"},
        "postal_code": {"zip", "postal", "postcode", "code"},
    }
    for key, key_aliases in aliases.items():
        if key in values and label_tokens & key_aliases:
            if key == "first_name" and "last" in label_tokens:
                continue
            if key == "last_name" and "first" in label_tokens:
                continue
            return values[key]
    for key, value in values.items():
        if key == "product":
            continue
        if tokens(key) & label_tokens:
            return value
    return None


def _first_clickable_matching(pam: PageAffordanceModel, wanted: set[str]) -> Affordance | None:
    for affordance in pam.clickable():
        if tokens(affordance.label) & wanted:
            return affordance
    return None


def _first_cart_link(pam: PageAffordanceModel) -> Affordance | None:
    for affordance in pam.clickable():
        selector = str(affordance.locator.get("selector", "")).lower()
        label = affordance.label.strip().lower()
        if "shopping_cart" in selector or "shopping-cart" in selector or label in {"cart", "shopping cart"}:
            return affordance
        if label.isdigit() and "cart" in selector:
            return affordance
    return None
