from __future__ import annotations

import json

from scripts.run_fadi_demo import DEMO_TITLE, booking_bindings, build_goal, build_parser, main, select_skill
from src.demos.registry import build_argv, find


def test_demo_goal_names_the_catalog_skill_and_structured_parameters() -> None:
    assert build_goal().parameters == {"room": "C", "time": "15:30"}

    goal = build_goal("B", "15:30")

    assert goal.goal_id == "confirm_booking"
    assert goal.goal_state == "booking.confirmed == true"
    assert goal.parameters == {"room": "B", "time": "15:30"}
    assert goal.source == "demo"

    selection = select_skill(goal)
    assert [condition.predicate for condition in selection.skill_tuple.preconditions] == ["booking.confirmed == false"]
    assert [condition.predicate for condition in selection.skill_tuple.postconditions] == ["booking.confirmed == true"]

    protected = next(binding for binding in booking_bindings() if binding.completion_for == "confirm_booking")
    assert protected.achieves == goal.goal_state
    assert protected.safety_level == "high"


def test_dry_run_emits_one_compact_end_to_end_evidence_artifact(tmp_path) -> None:
    evidence_path = tmp_path / "episode.json"

    exit_code = main(["--dry-run", "--output", str(evidence_path)])

    assert exit_code == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["title"] == DEMO_TITLE
    assert evidence["goal_spec"]["goal_id"] == "confirm_booking"
    assert evidence["selected_skill"]["skill_id"] == "confirm_booking"
    assert evidence["instantiated_skill_call"]["params"] == {"room": "C", "time": "15:30"}
    assert evidence["runtime_skill_selection"]["skill_id"] == "confirm_booking"
    assert evidence["runtime_skill_selection"]["validation_status"] == "passed"
    assert evidence["runtime_evidence_trace"][0]["event"] == "goal_skill_selection"
    assert [step["action"] for step in evidence["generated_primitive_plan"]] == ["type", "type", "click"]

    # The simulated human completes the protected click. The agent performs
    # only the two safe typing primitives, then re-observes and replans.
    assert [call["params"]["primitive_action"] for call in evidence["agent_executor_calls"]] == [
        "type",
        "type",
    ]
    intervention = evidence["human_interventions"][0]
    assert intervention["decision"] == "resume"
    assert intervention["correction_applied"] is True
    assert intervention["reobserved"] is True
    assert intervention["replanned"] is True

    assert evidence["result"]["state"] == "completed"
    assert evidence["result"]["final_outcome_verified"] is True
    assert evidence["result"]["intervention_replan_count"] == 1
    episode_id = evidence["result"]["episode_id"]
    assert episode_id
    assert len(evidence["transitions"]) == 2
    assert all(record["episode_id"] == episode_id for record in evidence["transitions"])
    assert intervention["episode_id"] == episode_id
    assert evidence["isolation"]["browser_context_generation"] == 1
    assert evidence["isolation"]["provider_active_after_run"] is False
    assert evidence["isolation"]["room_state_restored"] is True
    assert evidence["isolation"]["events"] == [
        "wot:acquire",
        "browser:recreate",
        "wot:restore",
        "browser:stop",
        "wot:release",
    ]

    assert (tmp_path / "transition_ledger.jsonl").is_file()
    assert (tmp_path / "intervention_ledger.jsonl").is_file()
    assert (tmp_path / "failure_ledger.jsonl").is_file()


def test_demo_registry_exposes_rehearsal_and_visible_walkthrough() -> None:
    rehearsal = find("supervised-pip-rehearsal")
    live = find("supervised-pip-live")

    assert rehearsal is not None
    assert build_argv(rehearsal)[-1] == "--dry-run"
    assert live is not None
    assert live.requires == ("browser", "smart_room")
    assert build_argv(live, headed=True)[-1] == "--headed"


def test_live_demo_has_an_adjustable_presentation_delay() -> None:
    assert build_parser().parse_args([]).step_delay == 1.2
    assert build_parser().parse_args(["--step-delay", "2.5"]).step_delay == 2.5
    assert main(["--dry-run", "--step-delay", "-1"]) == 2
