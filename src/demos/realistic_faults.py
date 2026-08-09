"""Faults that happen on real sites, named after why they happen.

The earlier faults teleported a button 150px or deleted it outright. They
produced a failure, but nothing on a real page fails that way, so recovering
from them proved little: the agent was solving a puzzle nobody had.

These are drawn from things that break production web automation every day, and
each one carries the reason it occurs so a viewer can place it. Two of the
classes match the taxonomy already used in evaluation/open_web_mock_failure_suite.py
(overlay_modal_obstruction, session_auth_expiry) so the vocabulary stays shared.

Difficulty is deliberately uneven. A layout shift is recoverable by looking
again; a consent banner needs the obstruction dealt with; a disabled control
needs a precondition satisfied first; an optimistic rollback cannot be seen at
all without re-reading the state afterwards. An agent that handles all of them
the same way is not diagnosing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Fault:
    """One realistic failure, with the real-world cause it stands for."""

    key: str
    name: str  # what a web engineer would call it
    real_cause: str  # why it happens in production
    symptom: str  # what the agent will observe
    difficulty: str  # easy | moderate | hard
    expected_cause: str  # correct diagnosis, for scoring only
    expected_tier: int  # correct recovery tier, for scoring only
    apply: Callable[[Any, str], bool]

    def blurb(self) -> str:
        return f"{self.name}\n\nWhy this happens in practice: {self.real_cause}"


# --- the injectors ------------------------------------------------------------
# Each mutates the page the way the real cause would, rather than in whatever
# way is easiest to detect.


def _layout_shift(session: Any, selector: str) -> bool:
    """Content loads above the target and pushes it down (Cumulative Layout Shift)."""
    return bool(
        session.evaluate(
            """(sel)=>{
                const el = document.querySelector(sel);
                if (!el) return false;
                const card = el.closest('article, .post-card, .product-card, div') || el.parentElement;
                const banner = document.createElement('div');
                banner.style.cssText = `height:120px;margin-bottom:12px;border-radius:10px;
                    background:linear-gradient(135deg,#fde68a,#fca5a5);display:flex;
                    align-items:center;justify-content:center;color:#7c2d12;
                    font:600 13px system-ui`;
                banner.textContent = 'Sponsored - image finished loading';
                card.parentElement.insertBefore(banner, card);
                return true;
            }""",
            selector,
        )
    )


def _consent_overlay(session: Any, selector: str) -> bool:
    """A consent banner mounts late and covers the target (overlay_modal_obstruction)."""
    return bool(
        session.evaluate(
            """(sel)=>{
                const el = document.querySelector(sel);
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const bar = document.createElement('div');
                bar.id = 'consent-banner';
                bar.style.cssText = `position:fixed;left:${r.left-30}px;top:${r.top-24}px;
                    width:${Math.max(r.width+60,300)}px;padding:18px 20px;z-index:9000;
                    background:#1e293b;color:#e2e8f0;border-radius:12px;
                    box-shadow:0 12px 40px rgba(0,0,0,.4);font:13px/1.5 system-ui`;
                bar.innerHTML = '<b>We value your privacy</b><br>' +
                    'We use cookies to improve your experience. ' +
                    '<button id="consent-accept" style="margin-top:10px;padding:6px 16px;' +
                    'border:none;border-radius:6px;background:#6366f1;color:#fff;' +
                    'font-weight:600;cursor:pointer">Accept all</button>';
                document.body.appendChild(bar);
                bar.querySelector('#consent-accept').addEventListener('click', ()=>bar.remove());
                return true;
            }""",
            selector,
        )
    )


def _disabled_until_valid(session: Any, selector: str) -> bool:
    """A required field is empty, so the control it gates is disabled.

    The most common disabled control on the web: the form will not submit until
    something it depends on is filled in. Nothing is broken, and no amount of
    clicking helps - the precondition has to be satisfied. The dependency is
    declared with aria-controls rather than left implicit, which is how a real
    accessible form states it and how an agent can find it without being told.

    Deliberately not on a timer. A control that re-enables itself after N
    seconds would make the recovery a matter of waiting long enough, which
    depends on how fast the demo happens to be running rather than on what the
    agent worked out.
    """
    return bool(
        session.evaluate(
            """(sel)=>{
                const el = document.querySelector(sel);
                if (!el) return false;
                const field = document.createElement('input');
                field.id = '__validation_qty';
                field.type = 'text';
                field.placeholder = 'Confirm quantity to continue';
                field.setAttribute('aria-label', 'Confirm quantity to continue');
                field.style.cssText = `width:100%;padding:7px 9px;margin-bottom:8px;
                    border:1.5px solid #f59e0b;border-radius:7px;font:13px system-ui`;
                el.parentElement.insertBefore(field, el);

                el.setAttribute('disabled', 'disabled');
                el.setAttribute('aria-disabled', 'true');
                el.setAttribute('aria-controls', '__validation_qty');
                el.style.opacity = '0.55';
                el.title = 'complete the required field first';

                field.addEventListener('input', ()=>{
                    if (!field.value.trim()) return;
                    el.removeAttribute('disabled');
                    el.removeAttribute('aria-disabled');
                    el.style.opacity = '';
                    el.title = 'validated';
                });
                return true;
            }""",
            selector,
        )
    )


def _optimistic_rollback(session: Any, selector: str) -> bool:
    """The UI confirms immediately, then the server rejects and it reverts."""
    return bool(
        session.evaluate(
            """(sel)=>{
                const el = document.querySelector(sel);
                if (!el) return false;
                el.addEventListener('click', ()=>{
                    // Optimistic: the page says it worked...
                    setTimeout(()=>{
                        // ...and the rollback lands before anyone verifies.
                        const cart = document.getElementById('cart-items');
                        if (cart) cart.innerHTML =
                            '<em style="color:#dc2626">Payment declined - item removed</em>';
                        const badge = document.getElementById('cart-badge');
                        if (badge) badge.style.display = 'none';
                    }, 350);
                }, true);
                return true;
            }""",
            selector,
        )
    )


def _session_expiry(session: Any, selector: str) -> bool:
    """The session has expired; acting replaces the application with a login wall.

    The whole application is torn down, not just the one control - that is what
    a redirect to a sign-in page does, and it is why no retry and no alternative
    affordance can reach the goal from here. The narration overlays are left in
    place because they are not part of the page under test.
    """
    return bool(
        session.evaluate(
            """(sel)=>{
                const el = document.querySelector(sel);
                if (!el) return false;
                el.addEventListener('click', (e)=>{
                    e.stopImmediatePropagation();
                    e.preventDefault();
                    document.querySelectorAll('body > *:not([id^="__cua"])')
                            .forEach(n => n.remove());
                    const wall = document.createElement('div');
                    wall.id = 'login-wall';
                    wall.style.cssText = `position:fixed;inset:0 430px 0 0;z-index:9500;
                        background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;
                        align-items:center;justify-content:center;gap:10px;font:15px system-ui`;
                    wall.innerHTML = '<b style="font-size:20px">Your session has expired</b>' +
                        '<div>Please sign in again to continue.</div>';
                    document.body.appendChild(wall);
                }, true);
                return true;
            }""",
            selector,
        )
    )


FAULTS: dict[str, Fault] = {
    "layout_shift": Fault(
        key="layout_shift",
        name="Layout shift (CLS)",
        real_cause="an image or ad above the target finishes loading without reserved space, "
        "so everything below it moves down. This is the most common cause of "
        "mis-clicks on real pages.",
        symptom="the target is still on the page, lower than where it was seen",
        difficulty="easy",
        expected_cause="target_moved",
        expected_tier=1,
        apply=_layout_shift,
    ),
    "consent_overlay": Fault(
        key="consent_overlay",
        name="Consent banner obstruction",
        real_cause="a cookie or privacy banner mounts asynchronously and lands on top of the "
        "control. The button is still there and still enabled; the click simply "
        "never reaches it.",
        symptom="the target is present and enabled, but something else receives the click",
        difficulty="moderate",
        expected_cause="target_occluded",
        expected_tier=2,
        apply=_consent_overlay,
    ),
    "disabled_until_valid": Fault(
        key="disabled_until_valid",
        name="Disabled by an unmet precondition",
        real_cause="a required field the control depends on has not been completed, so the "
        "form keeps it disabled. Nothing is broken; the precondition simply is "
        "not met yet, and clicking harder will never meet it.",
        symptom="the target is visible but refuses input, and declares what it is waiting on",
        difficulty="moderate",
        expected_cause="target_not_actionable",
        expected_tier=3,
        apply=_disabled_until_valid,
    ),
    "optimistic_rollback": Fault(
        key="optimistic_rollback",
        name="Optimistic UI rollback",
        real_cause="the interface confirms the action before the server has agreed, then "
        "reverts when the request is rejected. Anything that trusts the immediate "
        "response records a success that did not happen.",
        symptom="the action appears to succeed, and the state is undone a moment later",
        difficulty="hard",
        expected_cause="action_had_no_effect",
        expected_tier=4,
        apply=_optimistic_rollback,
    ),
    "session_expiry": Fault(
        key="session_expiry",
        name="Session expiry",
        real_cause="the authentication token expired between loading the page and acting on "
        "it, so the action is intercepted by a login wall instead of being applied.",
        symptom="acting replaces the page with a sign-in screen",
        difficulty="hard",
        expected_cause="target_vanished",
        expected_tier=4,
        apply=_session_expiry,
    ),
}


def difficulty_order() -> list[str]:
    """Fault keys from easiest to hardest, for ordering a demonstration."""
    rank = {"easy": 0, "moderate": 1, "hard": 2}
    return sorted(FAULTS, key=lambda k: (rank[FAULTS[k].difficulty], k))


__all__ = ["FAULTS", "Fault", "difficulty_order"]
