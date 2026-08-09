"""Task-level web planning over structured goals.

This layer answers a different question from the affordance planner:

* task planner: "what phases should this goal go through?"
* affordance planner: "given the current observed page, what is the next action?"

Keeping the two separate lets the runtime re-observe between actions while
still exposing a clear slot for a future LLM planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from src.perception.page_affordance_model import PageAffordanceModel

WebSubgoalKind = Literal[
    "login",
    "add_product",
    "open_cart",
    "checkout",
    "fill_checkout_info",
    "finish_order",
    "generic_goal",
]


@dataclass(frozen=True)
class WebSubgoal:
    id: str
    kind: WebSubgoalKind
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebTaskPlan:
    goal: str
    subgoals: list[WebSubgoal]
    planner: str = "rule"

    def current(self, index: int) -> WebSubgoal | None:
        if index < 0 or index >= len(self.subgoals):
            return None
        return self.subgoals[index]


class WebTaskPlanner(Protocol):
    def plan(self, goal: str, *, values: dict[str, Any] | None = None) -> WebTaskPlan: ...


class RuleBasedWebTaskPlanner:
    """Deterministic task decomposition for common web workflows.

    This is intentionally bounded. It does not infer a user's natural-language
    intent in the general case; it recognizes high-signal workflow words and
    turns them into explicit, inspectable subgoals.
    """

    def plan(self, goal: str, *, values: dict[str, Any] | None = None) -> WebTaskPlan:
        values = normalize_web_values(values or {})
        goal_tokens = tokens(goal)
        subgoals: list[WebSubgoal] = []

        has_credentials = "username" in values and "password" in values
        wants_login = bool(goal_tokens & {"login", "log", "signin", "sign", "buy", "purchase", "order", "checkout"})
        wants_purchase = bool(goal_tokens & {"buy", "purchase", "order", "cart", "checkout"})

        if has_credentials and wants_login:
            subgoals.append(
                WebSubgoal(
                    id="login",
                    kind="login",
                    description="Authenticate with the available credential fields.",
                )
            )

        if wants_purchase:
            subgoals.extend(
                [
                    WebSubgoal(
                        id="add_product",
                        kind="add_product",
                        description="Add the requested product to the cart.",
                        parameters={"product": values.get("product", "")},
                    ),
                    WebSubgoal(
                        id="open_cart",
                        kind="open_cart",
                        description="Open the shopping cart.",
                    ),
                    WebSubgoal(
                        id="checkout",
                        kind="checkout",
                        description="Enter the checkout flow.",
                    ),
                    WebSubgoal(
                        id="fill_checkout_info",
                        kind="fill_checkout_info",
                        description="Fill the checkout identity and postal fields.",
                    ),
                    WebSubgoal(
                        id="finish_order",
                        kind="finish_order",
                        description="Confirm the order from the checkout overview.",
                    ),
                ]
            )

        if not subgoals:
            subgoals.append(
                WebSubgoal(
                    id="generic_goal",
                    kind="generic_goal",
                    description=goal.strip() or "Interact with the page according to the current goal.",
                )
            )

        return WebTaskPlan(goal=goal, subgoals=subgoals)


class LLMWebTaskPlanner:
    """Reserved hook for future LLM-based task decomposition."""

    def plan(self, goal: str, *, values: dict[str, Any] | None = None) -> WebTaskPlan:
        _ = goal, values
        raise NotImplementedError("LLM task planner is intentionally left empty; use RuleBasedWebTaskPlanner for now")


def subgoal_satisfied(
    pam: PageAffordanceModel,
    page_text: str,
    subgoal: WebSubgoal,
    *,
    success_text: list[str] | None = None,
) -> bool:
    """White-box progress check for each bounded subgoal."""

    text = page_text.lower()
    url = pam.url.lower()
    success_fragments = [fragment.strip().lower() for fragment in success_text or [] if fragment.strip()]

    if subgoal.kind == "login":
        return "inventory" in url or "products" in text
    if subgoal.kind == "add_product":
        product_tokens = tokens(str(subgoal.parameters.get("product", "")))
        if "remove" in text and "cart" in text:
            return True
        if product_tokens:
            add_buttons = [
                aff
                for aff in pam.clickable()
                if {"add", "cart"} <= tokens(aff.label) and product_tokens & tokens(aff.label)
            ]
            return not add_buttons and _cart_has_items(pam, text)
        return _cart_has_items(pam, text)
    if subgoal.kind == "open_cart":
        return "cart" in url or "your cart" in text
    if subgoal.kind == "checkout":
        return "checkout" in url or "checkout: your information" in text
    if subgoal.kind == "fill_checkout_info":
        return "checkout-step-two" in url or "checkout: overview" in text
    if subgoal.kind == "finish_order":
        return bool(success_fragments) and all(fragment in text for fragment in success_fragments)
    return False


def normalize_web_values(values: dict[str, Any]) -> dict[str, Any]:
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


def tokens(text: str) -> set[str]:
    normalized = text.replace("_", " ").replace("-", " ").replace("/", " ").replace(".", " ").lower()
    return {token for token in normalized.split() if token}


def _cart_has_items(pam: PageAffordanceModel, page_text: str) -> bool:
    if "shopping cart badge" in page_text:
        return True
    for affordance in pam.clickable():
        selector = str(affordance.locator.get("selector", "")).lower()
        label = affordance.label.strip().lower()
        if label.isdigit() and int(label) > 0 and "cart" in selector:
            return True
    return False
