"""The LLM demo makes a claim on screen; these tests keep the claim true.

The demo says, in narration a viewer reads, that the rules handle scene 1 and
cannot handle scenes 2-4. That is a statement about ``rule_fallback``, not about
the demo, so it is checked against the real implementation here. If someone
broadens the patterns later, the demo would quietly become a lie about its own
system - this fails first instead.

No network and no browser: everything asserted here is either deterministic or a
property of the scene declarations.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from src.demos.model_panel import ModelPanel
from src.planner.device_binding import device_binding_for
from src.planner.environment_binding import binding_for
from src.planner.intent_planner import KNOWN_GOAL_STATES, GoalPlan, rule_fallback, rule_trace
from src.runtime.goal_spec import GoalSpec

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_llm_demo.py"
_spec = importlib.util.spec_from_file_location("run_llm_demo", _PATH)
assert _spec and _spec.loader
demo = importlib.util.module_from_spec(_spec)
sys.modules["run_llm_demo"] = demo
_spec.loader.exec_module(demo)


# ── the premise the narration states ────────────────────────────────────────────


def test_the_control_scene_really_is_one_the_rules_handle():
    """Scene 1 exists to show the model earning nothing, so the rules must earn it."""
    first = demo.SCENES[0]
    assert not first.expect_rules_to_fail
    assert rule_fallback(first.utterance).ok


def test_every_other_scene_really_is_beyond_the_rules():
    for scene in demo.SCENES[1:]:
        assert scene.expect_rules_to_fail, f"{scene.title} claims nothing about the rules"
        plan = rule_fallback(scene.utterance)
        assert not plan.ok, f"the rules now handle {scene.utterance!r}; the demo's contrast is gone"


def test_one_scene_makes_the_page_lie_and_only_one():
    faulted = [s for s in demo.SCENES if s.fault]
    assert len(faulted) == 1
    assert faulted[0].fault == "invisible_confirmation"


def test_every_scene_asks_for_something_this_room_can_do():
    """The demo acts on whatever the goal resolves to, so every scene must ground.

    A sentence naming something the room does not have would leave the model free
    to invent a subject, and the demo would spend its most-watched minute
    reporting that it could not find a control.
    """
    for scene in demo.SCENES:
        assert scene.expect_goal, f"{scene.title} does not say which goal it is for"
        assert scene.expect_goal in KNOWN_GOAL_STATES, f"{scene.expect_goal} is outside the closed vocabulary"
        served = binding_for(scene.expect_goal) is not None or device_binding_for(scene.expect_goal) is not None
        assert served, f"nothing in this room can satisfy {scene.expect_goal}"


def test_the_demo_touches_both_halves_of_the_use_case():
    """The use case is a digital surface over physical devices, not a web page.

    A run that only ever clicked the dashboard would be evidence for half of it,
    and the half that is easiest to mistake for an ordinary browser agent. One
    scene has to leave the browser and write to a Thing.
    """
    surfaces = {"device" if device_binding_for(s.expect_goal) else "dashboard" for s in demo.SCENES}
    assert surfaces == {"dashboard", "device"}, f"the scenes only exercise {surfaces}"


def test_no_goal_is_served_by_both_surfaces():
    """The runner checks the device table first, so an overlap would make which
    surface acts depend on that ordering rather than on the goal itself."""
    for goal_state in KNOWN_GOAL_STATES:
        both = binding_for(goal_state) is not None and device_binding_for(goal_state) is not None
        assert not both, f"{goal_state} is claimed by both the dashboard and the device tables"


# ── what the demo reports ───────────────────────────────────────────────────────


def _goal(state: str = "item_in_cart") -> GoalSpec:
    return GoalSpec(goal_id="g", goal_state=state, parameters={"item": "keyboard"}, description="d", source="test")


def test_a_missing_model_is_reported_as_missing_not_as_a_refusal():
    plan = GoalPlan(goal=None, source="unsupported", error="no model configured")
    assert demo.model_verdict(plan) == "no model configured"


def test_a_real_refusal_is_reported_as_one():
    plan = GoalPlan(goal=None, source="unsupported", error="")
    assert demo.model_verdict(plan) == "no supported goal"


def test_a_success_shows_the_goal_and_its_parameters():
    verdict = demo.model_verdict(GoalPlan(goal=_goal(), source="llm"))
    assert "item_in_cart" in verdict and "keyboard" in verdict


def test_the_summary_counts_what_each_path_achieved():
    run = demo.Run(
        scenes=[
            demo.SceneRecord(title="a", utterance="u", rules_goal="item_in_cart", model_goal="item_in_cart"),
            demo.SceneRecord(title="b", utterance="u", rules_goal="", model_goal="item_in_cart"),
            demo.SceneRecord(
                title="c", utterance="u", rules_goal="", model_goal="item_in_cart", caught_false_success=True
            ),
        ]
    )
    summary = run.to_dict()
    assert summary["rules_solved"] == 1
    assert summary["model_solved"] == 3
    assert summary["false_successes_caught"] == 1


def test_a_false_success_needs_both_a_dom_yes_and_a_visual_no():
    """The headline claim of the demo, so its condition is pinned rather than eyeballed."""
    record = demo.SceneRecord(title="t", utterance="u", dom_says_met=True, vision_answer=False)
    assert record.dom_says_met and record.vision_answer is False
    # An unusable judgement leaves vision_answer at None, which must not count.
    unusable = demo.SceneRecord(title="t", utterance="u", dom_says_met=True, vision_answer=None)
    assert not (unusable.dom_says_met and unusable.vision_answer is False)


# ── what the panel puts on screen ───────────────────────────────────────────────
# The panel is the whole deliverable of this demo: an earlier version narrated
# that a model was involved without showing anything it produced, and a viewer
# could not tell a model had run at all. These pin the evidence, not the wording.


class _FakePage:
    def __init__(self) -> None:
        self.html = ""

    def evaluate(self, expression: str, arg: object = None) -> bool:
        if isinstance(arg, dict) and "html" in arg:
            self.html = str(arg["html"])
        return True


def _panel() -> tuple[ModelPanel, _FakePage]:
    page = _FakePage()
    return ModelPanel(page), page


def test_the_rules_are_shown_running_not_summarised():
    panel, page = _panel()
    panel.begin_scene("SCENE 2/4", "grab me those headphones", "why")
    panel.show_rules(rule_trace("grab me those headphones"), "no goal", False)
    assert "cart" in page.html, "the patterns the rules tried must be on screen"
    assert "[  no ]" in page.html


def test_the_raw_reply_is_shown_verbatim():
    panel, page = _panel()
    panel.sending("qwen-plus", 'user: "grab me those headphones"')
    assert "waiting for the model" in page.html
    panel.reply(
        '{"goal_state": "item_in_cart"}',
        latency_ms=412.0,
        usage={"input": 300, "output": 90},
        verdict="item_in_cart",
        ok=True,
    )
    assert "item_in_cart" in page.html
    assert "412 ms" in page.html
    assert "300 in / 90 out tokens" in page.html


def test_the_image_sent_to_the_vision_model_is_shown_in_the_page():
    """A claim about a screenshot is not evidence; the screenshot is."""
    panel, page = _panel()
    panel.looking("qwen-vl-plus", "Does this show a cart entry?", b"\x89PNG\r\n\x1a\nfake")
    assert "data:image/png;base64,iVBORw0K" in page.html
    assert "Does this show a cart entry?" in page.html


def test_running_cost_accumulates_across_calls():
    panel, page = _panel()
    panel.reply("a", latency_ms=100.0, usage={"input": 10, "output": 5}, verdict="v", ok=True)
    panel.saw("b", latency_ms=900.0, usage={"input": 40, "output": 7}, verdict="v", ok=True)
    assert panel.totals == {"calls": 2, "in": 50, "out": 12, "ms": 1000.0}
    assert "50 in / 12 out" in page.html


def test_the_panel_never_breaks_the_run_when_the_page_rejects_it():
    class Broken:
        def evaluate(self, expression: str, arg: object = None) -> bool:
            raise RuntimeError("page navigated away")

    panel = ModelPanel(Broken())
    panel.begin_scene("s", "u", "w")  # must not raise
    assert panel.render() is False


def test_a_control_the_page_does_not_have_is_detected_before_it_is_clicked():
    class Page:
        def evaluate(self, expression: str, arg: object = None) -> bool:
            return arg == "button.add-cart-btn[data-id='keyboard']"

    assert demo.exists(Page(), "button.add-cart-btn[data-id='keyboard']")
    assert not demo.exists(Page(), "button.add-cart-btn[data-id='hovercraft']")
