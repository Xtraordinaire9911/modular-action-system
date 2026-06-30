"""Task definitions for the three WebArena-style local mock environments.

Each mock env is a self-contained static HTML page under env/mock_envs/.
All interactive elements (buttons, inputs) are always in the DOM and visible,
so MiniwobController's CSS-selector primitives work without waiting on JS reveals.
MockEnvController (defined in miniwob_tasks.py) is used: no #sync-task-cover gate,
no WOB_REWARD_GLOBAL — success is checked via page text after the solver runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MockEnvTask:
    name: str  # short identifier
    env_label: str  # shown in the in-page badge and console
    title: str  # human-facing description
    html_path: str  # filename under env/mock_envs/
    goal: str  # natural-language task objective
    solve: Callable[[Any], None]
    # Text that must appear in page body (case-insensitive) to count as success.
    # Empty string means "no exception during solve" counts as success.
    success_text: str = ""


# ── Shopping environment solvers ────────────────────────────────────────────────
def solve_shopping_add_checkout(c: Any) -> None:
    """Add headphones to cart, then proceed to checkout."""
    c.click_css("button.add-cart-btn[data-id='headphones']", "Add Wireless Headphones to cart")
    c.click_css("button#checkout-btn", "Proceed to checkout")


def solve_shopping_search_add(c: Any) -> None:
    """Search for 'laptop', then add it to cart."""
    c.fill("input#search-input", "laptop", 'Search for "laptop"')
    c.click_css("button#search-btn", "Submit search")
    c.click_css("button.add-cart-btn[data-id='laptop']", "Add Pro Laptop to cart")
    c.click_css("button#checkout-btn", "Proceed to checkout")


# ── Email environment solvers ────────────────────────────────────────────────────
def solve_email_reply_alice(c: Any) -> None:
    """Open Alice's email, write a reply, and send it."""
    c.click_css("div.email-item[data-id='alice']", "Open Alice's message")
    c.click_css("button#reply-btn", "Click Reply")
    c.fill("textarea#reply-input", "Thanks for your message! Happy to schedule a call.", "Type reply")
    c.click_css("button#send-btn", "Send reply")


def solve_email_archive_bob(c: Any) -> None:
    """Open Bob's email and archive it."""
    c.click_css("div.email-item[data-id='bob']", "Open Bob's message")
    c.click_css("button#archive-btn", "Archive the email")


# ── Forum environment solvers ────────────────────────────────────────────────────
def solve_forum_upvote_top(c: Any) -> None:
    """Upvote the top-ranked forum post."""
    c.click_css("button.upvote-btn[data-post='1']", "Upvote the top post")


def solve_forum_new_post(c: Any) -> None:
    """Create a new forum post with a title and body."""
    c.fill("input#post-title", "Hello from the Agent!", "Enter post title")
    c.fill("textarea#post-content", "This post was created autonomously by the action system.", "Enter content")
    c.click_css("button#submit-post-btn", "Submit new post")


# ── Curated mock-env demo suite ──────────────────────────────────────────────────
MOCK_TASKS: list[MockEnvTask] = [
    MockEnvTask(
        "shopping-add-checkout",
        "WebArena-style Shopping",
        "Add headphones to cart and checkout",
        "shopping.html",
        "Add Wireless Headphones to the cart and proceed to checkout",
        solve_shopping_add_checkout,
        success_text="order confirmed",  # overlay text on checkout
    ),
    MockEnvTask(
        "shopping-search-add",
        "WebArena-style Shopping",
        "Search for laptop and purchase it",
        "shopping.html",
        "Search for laptop, add it to cart, and checkout",
        solve_shopping_search_add,
        success_text="order confirmed",
    ),
    MockEnvTask(
        "email-reply-alice",
        "WebArena-style Email",
        "Reply to Alice's project update message",
        "email_inbox.html",
        "Open Alice's email and send a reply",
        solve_email_reply_alice,
        success_text="sent",  # "Message sent!" notice
    ),
    MockEnvTask(
        "email-archive-bob",
        "WebArena-style Email",
        "Archive Bob's meeting reminder",
        "email_inbox.html",
        "Open Bob's email and archive it",
        solve_email_archive_bob,
        success_text="",  # visual-only (strikethrough), no text token needed
    ),
    MockEnvTask(
        "forum-upvote",
        "WebArena-style Forum",
        "Upvote the top-ranked post",
        "forum.html",
        "Find the highest-scored post and upvote it",
        solve_forum_upvote_top,
        success_text="",  # visual change only (button colour + count)
    ),
    MockEnvTask(
        "forum-new-post",
        "WebArena-style Forum",
        "Create a new forum post",
        "forum.html",
        "Write and submit a new post to the forum",
        solve_forum_new_post,
        success_text="posted successfully",  # notice text
    ),
]
