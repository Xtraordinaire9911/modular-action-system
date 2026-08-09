"""The claims that can only be checked against a real browser.

    pytest -m live

The rest of the suite runs in about five seconds and never opens a browser,
never starts Docker, and never talks to a device. That is fine for what it
covers, but it means "all tests pass" says nothing about live behaviour - and
live behaviour is what every headline claim in this repository is about. A
reviewer told this team once already that they did not believe the tests proved
what was being claimed; a suite that cannot fail when the browser misbehaves is
that objection in a new form.

Each test here asserts one claim that the README or STATUS.md makes, against a
real page, and would fail if the claim stopped being true.
"""

from __future__ import annotations

import pytest

from scripts.run_agent_on_env import _start_static_server
from src.perception.dom_transducer import DomTransducer
from src.perception.som_parser import marks_from_affordances

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def server():
    httpd, port = _start_static_server("env/mock_envs")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@pytest.fixture
def session(server):
    from src.perception.browser_session import BrowserSession

    live = BrowserSession.launch(f"{server}/shopping.html", headless=True)
    live.content_html = lambda: live._page.content()
    try:
        yield live
    finally:
        live.close()


def _pam(session):
    return DomTransducer().transduce(session.content_html(), page_id="live")


# --- perception -----------------------------------------------------------------


def test_every_derived_selector_matches_exactly_one_element(session):
    """Claim: selectors are locators. A shared one silently measures the wrong element."""
    counts = {
        affordance.locator["selector"]: session.evaluate(
            "(s)=>document.querySelectorAll(s).length", affordance.locator["selector"]
        )
        for affordance in _pam(session).affordances
    }
    shared = {selector: n for selector, n in counts.items() if n != 1}

    assert not shared, f"selectors matching other than one element: {shared}"


def test_geometry_is_measured_in_the_browser_and_distinct(session):
    """Claim: marks describe geometry we observed, never a fixture."""
    from src.perception.visual_geometry import attach_measured_bboxes

    pam = _pam(session)
    measured = attach_measured_bboxes(pam, session)
    marks = marks_from_affordances(pam.affordances)
    centres = {mark.bbox.center for mark in marks}

    assert measured == len(marks) > 0
    assert len(centres) == len(marks), "two marks share a centre, so at least one box is not real"


def test_an_element_that_cannot_be_measured_gets_no_mark(session):
    """Claim: the agent cannot aim at something imagined."""
    from src.perception.visual_geometry import attach_measured_bboxes

    session.evaluate("()=>{document.querySelector('#checkout-btn').style.display='none';}")
    pam = _pam(session)
    attach_measured_bboxes(pam, session)
    marks = marks_from_affordances(pam.affordances)

    assert not any("checkout" in mark.label.lower() for mark in marks)


# --- episode isolation ------------------------------------------------------------


def test_a_new_episode_does_not_inherit_the_previous_one_s_state(session):
    """Claim: episode isolation is real, and reset() is not the same thing."""
    session.evaluate("() => { localStorage.setItem('probe', 'leaked'); }")
    session.reset()
    after_reset = session.evaluate("() => localStorage.getItem('probe')")
    session.new_episode()
    after_episode = session.evaluate("() => localStorage.getItem('probe')")

    assert after_reset == "leaked", "reset() is documented as leaking; if it stopped, the docs are wrong"
    assert after_episode is None, "a new episode inherited state from the previous one"


# --- verification ------------------------------------------------------------------


def test_verification_reads_the_region_the_goal_names_not_the_page(session):
    """Claim: a body-text check would pass before the agent acts."""
    body = (session.evaluate("()=>document.body.innerText") or "").lower()
    cart = (session.evaluate("()=>document.querySelector('#cart-items').innerText") or "").lower()

    assert "wireless headphones" in body, "the product title is on the page before anything is added"
    assert "wireless headphones" not in cart, "the cart is empty, so a region check correctly fails"


def test_the_goal_state_actually_changes_when_the_agent_acts(session):
    """Claim: the loop verifies by re-observing, and the effect is observable."""
    from src.perception.visual_geometry import attach_measured_bboxes

    pam = _pam(session)
    attach_measured_bboxes(pam, session)
    target = next(m for m in marks_from_affordances(pam.affordances) if "headphones" in m.label.lower())
    session.click_xy(*target.bbox.center)

    cart = (session.evaluate("()=>document.querySelector('#cart-items').innerText") or "").lower()
    assert "wireless headphones" in cart


# --- the probes the diagnosis rests on ----------------------------------------------


def test_probes_measure_a_real_obstruction(session):
    """Claim: an occluded control is distinguished by measurement, not inference."""
    from src.demos.probes import hit_test, interactability, occlusion
    from src.demos.realistic_faults import FAULTS

    selector = "button.add-cart-btn[data-id='headphones']"
    assert FAULTS["consent_overlay"].apply(session, selector)

    covered = occlusion(session, selector)
    reachable = interactability(session, selector)
    box = session.evaluate(
        "(s)=>{const r=document.querySelector(s).getBoundingClientRect();"
        "return [Math.round(r.left+r.width/2), Math.round(r.top+r.height/2)];}",
        selector,
    )
    hit = hit_test(session, box[0], box[1], selector)

    assert covered.ok and covered.covered, "the banner is on top of the target and was not detected"
    assert reachable.ok and reachable.actionable, "the target is still enabled; only the click is blocked"
    assert hit.ok and not hit.is_target


def test_probes_report_when_they_cannot_run(session):
    """Claim: a probe that cannot run says so rather than returning a finding."""
    from src.demos.probes import interactability, occlusion

    missing = "button#does-not-exist"

    assert interactability(session, missing).exists is False
    assert occlusion(session, missing).missing is True
