import json

from evaluation.adaptation_demo import run_adaptation_demo


def test_adaptation_demo_writes_white_box_runtime_artifacts(tmp_path):
    paths = run_adaptation_demo(tmp_path)

    runtime = json.loads(paths["runtime_demo"].read_text(encoding="utf-8"))
    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    proposals = json.loads(paths["policy_proposals"].read_text(encoding="utf-8"))
    scenarios = {scenario["name"]: scenario for scenario in runtime["scenarios"]}

    assert scenarios["sensory_conflict_blocks_system1"]["state"] == "escalated"
    assert scenarios["sensory_conflict_blocks_system1"]["fusion_decision"]["allow_system1"] is False
    assert scenarios["sensory_conflict_resolved_by_active_perception"]["state"] == "completed"
    assert scenarios["sensory_conflict_resolved_by_active_perception"]["active_perception_trace"][0]["resolved"] is True
    assert scenarios["bounded_goal_executes_without_durable_skill"]["state"] == "completed"
    assert [step["action"] for step in scenarios["bounded_goal_executes_without_durable_skill"]["primitive_plan"]] == [
        "type",
        "type",
        "click",
    ]
    assert scenarios["executor_timeout_reroutes_with_trace"]["failure_boundary"] == "immediate_runtime_error"
    assert scenarios["executor_timeout_reroutes_with_trace"]["recovery_trace"][1]["policy"] == "reroute"
    assert scenarios["optional_llm_advisory_judgment"]["llm_failure_boundary"] == "skill_spec_insufficient"

    assert metrics["CascadeTraceCoverage"] > 0
    assert metrics["BoundaryClassificationRate"] > 0
    assert report["summary"]["policy_proposals"] == 1
    assert proposals["proposals"][0]["release_gate"]["approved"] is False
    assert paths["ledger"].exists()
