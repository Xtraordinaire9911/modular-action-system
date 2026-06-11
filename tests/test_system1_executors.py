"""Tests for System-1 executors, reflex library, and the VAM adapter (Member B)."""

from __future__ import annotations

from src.contracts.types import Affordance, ExecutionResult, SkillCall
from src.effectors.dom_executor import DomExecutor
from src.effectors.system1_reflex_library import System1ReflexLibrary
from src.effectors.visual_executor import VisualExecutor
from src.effectors.wot_executor import RateLimitExceeded, WotExecutor
from src.vam.vam_adapter import VamAdapter
from src.vam.vam_payload import VAMRecoveryPayload


# ── fakes ───────────────────────────────────────────────────────────────────
class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector, None))

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))


class FakePointer:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []

    def click_xy(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def type_text(self, text: str) -> None:
        self.typed.append(text)


def _dom_aff(action="click", enabled=True):
    return Affordance("dom_book", "DOM", "button", "Book", action,
                      {"selector": "#book"}, 1.0, {"enabled": enabled})


# ── DOM executor ──────────────────────────────────────────────────────────────
def test_dom_executor_click_returns_success():
    page = FakePage()
    res = DomExecutor(page).execute(_dom_aff(), skill_id="confirm_booking")
    assert res.success and res.backend_used == "dom"
    assert page.calls == [("click", "#book", None)]


def test_dom_executor_type_fills_value():
    page = FakePage()
    aff = Affordance("dom_temp", "DOM", "input", "Temp", "type", {"selector": "#t"}, 0.97)
    res = DomExecutor(page).execute(aff, value=22)
    assert res.success and page.calls == [("fill", "#t", "22")]


def test_dom_executor_disabled_element_fails_gracefully():
    res = DomExecutor(FakePage()).execute(_dom_aff(enabled=False))
    assert not res.success and "disabled" in res.failure_reason


# ── WoT executor ──────────────────────────────────────────────────────────────
def _wot_aff(method="POST", rate_ms=0.0):
    return Affordance(
        "wot_thermostat_A_setTargetTemperature", "WOT", "action", "setTargetTemperature", "invoke",
        {"thing_id": "thermostat_A", "href": "http://h/actions/set", "method": method},
        1.0, {"content_type": "application/json", "rate_limit": {"min_interval_ms": rate_ms}},
    )


def test_wot_executor_uses_td_href_and_method_no_hardcoding():
    seen = {}

    def fake_send(method, url, **kw):
        seen.update(method=method, url=url, json=kw.get("json"))
        return 200, {"ok": True}

    res = WotExecutor(send=fake_send).execute(_wot_aff(), value=22)
    assert res.success
    assert seen == {"method": "POST", "url": "http://h/actions/set", "json": 22}


def test_wot_executor_http_error_is_failure():
    res = WotExecutor(send=lambda *a, **k: (503, "down")).execute(_wot_aff())
    assert not res.success and "503" in res.failure_reason


def test_wot_executor_enforces_rate_limit():
    clock = iter([0.0, 0.0, 0.0])  # both calls "now" → 2nd within window
    gate_clock = lambda: next(clock)  # noqa: E731
    from src.effectors.wot_executor import _MinIntervalGate

    ex = WotExecutor(send=lambda *a, **k: (200, 1), gate=_MinIntervalGate(clock=gate_clock))
    aff = _wot_aff(rate_ms=6000.0)
    assert ex.execute(aff).success  # first call ok
    second = ex.execute(aff)  # too soon → RateLimitExceeded → failed result
    assert not second.success and "interval" in second.failure_reason
    assert RateLimitExceeded  # symbol exported


# ── Visual executor ─────────────────────────────────────────────────────────
def test_visual_executor_clicks_bbox_center():
    pointer = FakePointer()
    aff = Affordance("vis_M0", "VISUAL", "button", "Book", "click",
                     {"mark_id": "M0", "bbox": [410, 220, 110, 40], "center": [465, 240]}, 0.93)
    res = VisualExecutor(pointer).execute(aff)
    assert res.success and pointer.clicks == [(465, 240)]


# ── Reflex library ────────────────────────────────────────────────────────────
def test_reflex_library_caches_and_recalls_best_grounding():
    lib = System1ReflexLibrary()
    lib.remember("set_temperature", Affordance("wot_x", "WOT", "action", "x", "invoke", {"href": "h"}, 1.0))
    lib.remember("set_temperature", Affordance("dom_x", "DOM", "input", "x", "type", {"selector": "#x"}, 0.8))
    recalled = lib.recall("set_temperature")
    assert recalled.source == "WOT"  # higher confidence wins


def test_reflex_declines_low_confidence_and_escalates():
    lib = System1ReflexLibrary()
    skill = SkillCall("set_temperature", {"target": 22})
    low = Affordance("dom_x", "DOM", "input", "x", "type", {"selector": "#x"}, 0.5)
    outcome = lib.run(skill, low, executor=lambda *a, **k: ExecutionResult("x", "dom", True, 1.0, 0.5))
    assert outcome.escalate and outcome.escalation_reason == "low_confidence"


def test_reflex_fast_path_within_budget():
    lib = System1ReflexLibrary()
    skill = SkillCall("confirm_booking", {})
    aff = _dom_aff()
    ok = ExecutionResult("confirm_booking", "dom", True, 3.0, 1.0)
    outcome = lib.run(skill, aff, executor=lambda *a, **k: ok)
    assert outcome.fast_path and outcome.within_budget and not outcome.escalate


def test_needs_system2_trigger_matrix():
    lib = System1ReflexLibrary()
    assert lib.needs_system2(confidence=0.95) == (False, None)
    assert lib.needs_system2(confidence=0.95, postcondition_passed=False)[1] == "postcondition_failed"
    assert lib.needs_system2(confidence=0.95, selector_failed=True)[1] == "selector_failed"
    assert lib.needs_system2(confidence=0.95, backend_available=False)[1] == "backend_unavailable"


# ── VAM adapter ───────────────────────────────────────────────────────────────
def test_vam_adapter_should_invoke_conditions():
    vam = VamAdapter()
    assert vam.should_invoke(confidence=0.5)
    assert vam.should_invoke(postcondition_passed=False)
    assert not vam.should_invoke()


def test_vam_adapter_offline_heuristic_selects_label_match():
    vam = VamAdapter()
    cands = [
        Affordance("vis_M0", "VISUAL", "button", "Cancel", "click", {"mark_id": "M0", "bbox": [0, 0, 10, 10]}, 0.95),
        Affordance("vis_M1", "VISUAL", "button", "Book Room", "click", {"mark_id": "M1", "bbox": [1, 1, 10, 10]}, 0.80),
    ]
    payload = VAMRecoveryPayload(SkillCall("book_room", {}), "selector_failed", candidate_affordances=cands)
    grounding = vam.recover(payload)
    assert grounding is not None and grounding.mark_id == "M1"  # "book"/"room" token match beats confidence
