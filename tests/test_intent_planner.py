"""The intent layer must never let a fallback pass as interpretation.

This layer is the one the review flagged as missing, so the property that
matters most is not accuracy - it is that a reader can always tell whether a
GoalSpec came from a model or from pattern matching. A fallback that reads as
interpretation would make the agent claim unverifiable.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.planner.intent_planner import (
    KNOWN_GOAL_STATES,
    GoalPlan,
    IntentPlanner,
    rule_fallback,
)


class FakeClient:
    """Returns a scripted reply, so parsing is tested without a network call."""

    def __init__(self, reply: str, *, name: str = "fake-model") -> None:
        self.name = name
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


class BrokenClient:
    name = "broken-model"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("upstream refused the request")


def _reply(**overrides) -> str:
    payload = {
        "goal_state": "temperature_set",
        "parameters": {"room": "A", "degrees": 22},
        "description": "Set room A to 22 degrees",
        "success_evidence": ["thermostat reports 22"],
        "safety_constraints": ["do not exceed 26"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _planner(client, tmp_path: Path) -> IntentPlanner:
    return IntentPlanner(client=client, ledger_path=tmp_path / "calls.jsonl")


# --- provenance is never ambiguous ---------------------------------------------


def test_model_result_is_marked_as_model_derived(tmp_path):
    plan = _planner(FakeClient(_reply()), tmp_path).plan("make room A 22 degrees")

    assert plan.source == "llm"
    assert plan.is_model_derived is True
    assert plan.model == "fake-model"


def test_fallback_is_never_reported_as_model_derived(tmp_path):
    plan = _planner(None, tmp_path).plan("set the room to 22 degrees")

    assert plan.source == "rule_fallback"
    assert plan.is_model_derived is False, "a fallback must never read as interpretation"


def test_model_failure_falls_back_but_records_the_error(tmp_path):
    """A model failure must not silently become a model success."""
    plan = _planner(BrokenClient(), tmp_path).plan("set the room to 22 degrees")

    assert plan.is_model_derived is False
    assert "upstream refused" in plan.error


def test_fallback_can_be_refused_outright(tmp_path):
    planner = IntentPlanner(client=None, ledger_path=tmp_path / "c.jsonl", allow_fallback=False)
    plan = planner.plan("set the room to 22 degrees")

    assert plan.ok is False and plan.source == "unsupported"


# --- parsing the model's answer -------------------------------------------------


def test_goal_spec_carries_parameters_evidence_and_constraints(tmp_path):
    plan = _planner(FakeClient(_reply()), tmp_path).plan("make room A 22 degrees")

    assert plan.goal is not None
    assert plan.goal.goal_state == "temperature_set"
    assert plan.goal.parameters == {"room": "A", "degrees": 22}
    assert plan.goal.success_evidence == ["thermostat reports 22"]
    assert plan.goal.safety_constraints == ["do not exceed 26"]
    assert plan.confidence == 0.9


def test_fenced_json_is_accepted(tmp_path):
    fenced = f"Here you go:\n```json\n{_reply()}\n```\nHope that helps."
    plan = _planner(FakeClient(fenced), tmp_path).plan("make room A 22 degrees")

    assert plan.is_model_derived and plan.goal is not None


def test_unknown_goal_state_is_rejected_rather_than_passed_through(tmp_path):
    """A closed vocabulary is what the layer below relies on."""
    plan = _planner(FakeClient(_reply(goal_state="launch_rocket")), tmp_path).plan("launch a rocket")

    assert plan.ok is False
    assert plan.source == "unsupported"
    assert "launch_rocket" in plan.error


def test_model_may_decline_an_unsupported_request(tmp_path):
    plan = _planner(FakeClient(_reply(goal_state="unsupported")), tmp_path).plan("write me a poem")
    assert plan.ok is False and plan.source == "unsupported"


def test_unparseable_reply_does_not_raise(tmp_path):
    plan = _planner(FakeClient("I'm not going to answer in JSON"), tmp_path).plan("set 22 degrees")

    assert plan.is_model_derived is False
    assert plan.error


# --- the deterministic fallback ------------------------------------------------


def test_fallback_recognises_a_few_phrasings():
    assert rule_fallback("set room A to 22 degrees").goal.goal_state == "temperature_set"
    assert rule_fallback("turn the projector on").goal.goal_state == "projector_on"
    assert rule_fallback("archive that message").goal.goal_state == "message_archived"
    assert rule_fallback("add the headphones to the cart").goal.goal_state == "item_in_cart"


def test_fallback_extracts_the_room_and_the_value():
    goal = rule_fallback("set room B to 24 degrees").goal
    assert goal.parameters["degrees"] == 24 and goal.parameters["room"] == "B"


def test_fallback_confidence_stays_low_because_it_is_not_understanding():
    assert rule_fallback("set room A to 22 degrees").confidence <= 0.5


def test_fallback_admits_when_it_recognises_nothing():
    plan = rule_fallback("could you handle the thing we discussed")
    assert plan.ok is False and plan.source == "unsupported"


# --- the audit ledger -----------------------------------------------------------


def test_every_call_is_written_to_the_ledger(tmp_path):
    ledger = tmp_path / "calls.jsonl"
    planner = IntentPlanner(client=FakeClient(_reply()), ledger_path=ledger)

    planner.plan("make room A 22 degrees")
    planner.plan("turn the projector on")

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["intent"] == "make room A 22 degrees"
    assert first["source"] == "llm"
    assert first["prompt"] and first["raw_response"], "prompt and reply must both be auditable"


def test_ledger_records_the_fallback_path_too(tmp_path):
    ledger = tmp_path / "calls.jsonl"
    IntentPlanner(client=None, ledger_path=ledger).plan("set 22 degrees")

    record = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert record["source"] == "rule_fallback"


def test_logging_failure_never_breaks_planning(tmp_path):
    planner = IntentPlanner(client=FakeClient(_reply()), ledger_path=tmp_path / "no" / "such" / "x.jsonl")
    planner.ledger_path = Path("\0invalid")  # unwritable on every platform

    assert planner.plan("make room A 22 degrees").ok is True


# --- the layer stays separate ---------------------------------------------------


def test_planner_emits_a_goal_and_nothing_else(tmp_path):
    """No skills, no effectors: the layer below owns those decisions."""
    plan = _planner(FakeClient(_reply()), tmp_path).plan("make room A 22 degrees")

    assert isinstance(plan, GoalPlan)
    assert not hasattr(plan, "skills") and not hasattr(plan, "actions")


def test_vocabulary_is_closed_and_documented():
    assert "temperature_set" in KNOWN_GOAL_STATES
    assert all(state.islower() for state in KNOWN_GOAL_STATES)
