"""Rule-first web task planner over observed page affordances.

This module upgrades the external-site demo from scripted Playwright steps to
an observe-plan-act loop. The planner never emits raw selectors; it selects an
observed ``Affordance`` and an optional value. An LLM planner can later be added
behind the same decision contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from src.contracts.types import Affordance
from src.perception.page_affordance_model import PageAffordanceModel


@dataclass(frozen=True)
class WebPlannerDecision:
    affordance: Affordance | None
    value: Any | None = None
    reason: str = ""
    done: bool = False
    page_url: str = ""


@dataclass(frozen=True)
class WebPlannerHistory:
    steps: list[WebPlannerDecision] = field(default_factory=list)

    def append(self, decision: WebPlannerDecision) -> "WebPlannerHistory":
        return WebPlannerHistory([*self.steps, decision])


class WebTaskPlanner(Protocol):
    def next_action(
        self,
        pam: PageAffordanceModel,
        goal: str,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision: ...


class RuleBasedWebPlanner:
    """Deterministic multi-step planner for form and checkout-like tasks."""

    def next_action(
        self,
        pam: PageAffordanceModel,
        goal: str,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision:
        values = _normalize_values(values or {})
        history = history or WebPlannerHistory()
        goal_tokens = _tokens(goal)

        input_decision = _next_unfilled_input(pam, values, history)
        if input_decision is not None:
            return input_decision

        for matcher in (
            _login_action,
            _add_to_cart_action,
            _checkout_action,
            _continue_action,
            _finish_action,
            _open_cart_action,
        ):
            decision = matcher(pam, goal_tokens, values)
            if decision.affordance is not None:
                return decision

        return WebPlannerDecision(None, reason="no rule matched current observed affordances", done=True)


class LLMWebPlanner:
    """Placeholder for a future LLM planner using the same decision contract."""

    def next_action(
        self,
        pam: PageAffordanceModel,
        goal: str,
        *,
        values: dict[str, Any] | None = None,
        history: WebPlannerHistory | None = None,
    ) -> WebPlannerDecision:
        _ = pam, goal, values, history
        raise NotImplementedError("LLM web planner is intentionally left empty; use RuleBasedWebPlanner for now")


def _next_unfilled_input(
    pam: PageAffordanceModel,
    values: dict[str, Any],
    history: WebPlannerHistory,
) -> WebPlannerDecision | None:
    used_inputs = {
        decision.affordance.id
        for decision in history.steps
        if decision.affordance is not None
        and decision.affordance.action in {"type", "select"}
        and decision.page_url == pam.url
    }
    for affordance in pam.inputs():
        if affordance.id in used_inputs:
            continue
        value = _value_for_input(affordance, values)
        if value is not None:
            return WebPlannerDecision(
                affordance,
                value=value,
                reason=f"fill observed input '{affordance.label}'",
                page_url=pam.url,
            )
    return None


def _login_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    has_login_intent = bool(goal_tokens & {"login", "log", "buy", "purchase", "order", "checkout"})
    has_credentials = "username" in values and "password" in values
    if not has_login_intent and not has_credentials:
        return WebPlannerDecision(None)
    affordance = _first_clickable_matching(pam, {"login", "log-in", "sign-in", "submit"})
    if affordance is None:
        return WebPlannerDecision(None)
    return WebPlannerDecision(affordance, reason="login form appears ready", page_url=pam.url)


def _add_to_cart_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    if not (goal_tokens & {"buy", "purchase", "order", "cart", "checkout", "add"}):
        return WebPlannerDecision(None)
    product_tokens = _tokens(str(values.get("product", "")))
    candidates = [
        affordance
        for affordance in pam.clickable()
        if "add" in _tokens(affordance.label) and "cart" in _tokens(affordance.label)
    ]
    if not candidates:
        return WebPlannerDecision(None)
    if product_tokens:
        candidates.sort(key=lambda aff: len(product_tokens & _tokens(aff.label)), reverse=True)
        if len(product_tokens & _tokens(candidates[0].label)) == 0:
            return WebPlannerDecision(None)
    return WebPlannerDecision(candidates[0], reason="add matching observed product to cart", page_url=pam.url)


def _open_cart_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    _ = values
    if not (goal_tokens & {"buy", "purchase", "order", "cart", "checkout"}):
        return WebPlannerDecision(None)
    if _has_clickable_matching(pam, {"checkout"}):
        return WebPlannerDecision(None)
    affordance = _first_cart_link(pam)
    if affordance is None:
        return WebPlannerDecision(None)
    return WebPlannerDecision(affordance, reason="open cart before checkout", page_url=pam.url)


def _checkout_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    _ = values
    if not (goal_tokens & {"buy", "purchase", "order", "checkout"}):
        return WebPlannerDecision(None)
    affordance = _first_clickable_matching(pam, {"checkout"})
    if affordance is None:
        return WebPlannerDecision(None)
    return WebPlannerDecision(affordance, reason="checkout action available", page_url=pam.url)


def _continue_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    _ = goal_tokens, values
    affordance = _first_clickable_matching(pam, {"continue"})
    if affordance is None:
        return WebPlannerDecision(None)
    return WebPlannerDecision(affordance, reason="continue checkout after filling details", page_url=pam.url)


def _finish_action(
    pam: PageAffordanceModel,
    goal_tokens: set[str],
    values: dict[str, Any],
) -> WebPlannerDecision:
    _ = values
    if not (goal_tokens & {"buy", "purchase", "order", "checkout", "finish", "complete"}):
        return WebPlannerDecision(None)
    affordance = _first_clickable_matching(pam, {"finish", "place", "submit", "complete"})
    if affordance is None:
        return WebPlannerDecision(None)
    return WebPlannerDecision(affordance, reason="finish final checkout step", page_url=pam.url)


def _value_for_input(affordance: Affordance, values: dict[str, Any]) -> Any | None:
    label_tokens = _tokens(" ".join([affordance.id, affordance.label, str(affordance.locator.get("selector", ""))]))
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
        if _tokens(key) & label_tokens:
            return value
    return None


def _first_clickable_matching(pam: PageAffordanceModel, wanted: set[str]) -> Affordance | None:
    for affordance in pam.clickable():
        if _tokens(affordance.label) & wanted:
            return affordance
    return None


def _has_clickable_matching(pam: PageAffordanceModel, wanted: set[str]) -> bool:
    return _first_clickable_matching(pam, wanted) is not None


def _first_cart_link(pam: PageAffordanceModel) -> Affordance | None:
    for affordance in pam.clickable():
        selector = str(affordance.locator.get("selector", "")).lower()
        label = affordance.label.strip().lower()
        if "shopping_cart" in selector or "shopping-cart" in selector or label in {"cart", "shopping cart"}:
            return affordance
        if label.isdigit() and "cart" in selector:
            return affordance
    return None


def _normalize_values(values: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in values.items():
        norm = key.strip().replace("-", "_").replace(" ", "_").lower()
        if norm in {"first", "firstname", "first_name"}:
            norm = "first_name"
        elif norm in {"last", "lastname", "last_name"}:
            norm = "last_name"
        elif norm in {"zip", "zipcode", "postal", "postal_code", "postcode"}:
            norm = "postal_code"
        elif norm in {"user", "user_name"}:
            norm = "username"
        normalized[norm] = value
    return normalized


def _tokens(text: str) -> set[str]:
    normalized = text.replace("_", " ").replace("-", " ").replace("/", " ").replace(".", " ").lower()
    return {token for token in normalized.split() if token}
