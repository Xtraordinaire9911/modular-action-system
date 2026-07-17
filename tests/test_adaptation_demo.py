import json

from evaluation.adaptation_demo import run_adaptation_demo


def test_adaptation_demo_writes_white_box_runtime_artifacts(tmp_path):
    paths = run_adaptation_demo(tmp_path)

    runtime = json.loads(paths["runtime_demo"].read_text(encoding="utf-8"))
    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    proposals = json.loads(paths["policy_proposals"].read_text(encoding="utf-8"))

    assert runtime["scenarios"][0]["name"] == "sensory_conflict_blocks_system1"
    assert runtime["scenarios"][0]["state"] == "escalated"
    assert runtime["scenarios"][1]["name"] == "sensory_conflict_resolved_by_active_perception"
    assert runtime["scenarios"][1]["state"] == "completed"
    assert runtime["scenarios"][1]["active_perception_trace"][0]["resolved"] is True
    assert runtime["scenarios"][2]["name"] == "executor_timeout_reroutes_with_trace"
    assert runtime["scenarios"][2]["failure_boundary"] == "immediate_runtime_error"
    assert runtime["scenarios"][2]["recovery_trace"][1]["policy"] == "reroute"
    assert runtime["scenarios"][3]["name"] == "optional_llm_advisory_judgment"
    assert runtime["scenarios"][3]["llm_failure_boundary"] == "skill_spec_insufficient"

    assert metrics["CascadeTraceCoverage"] > 0
    assert metrics["BoundaryClassificationRate"] > 0
    assert report["summary"]["policy_proposals"] == 1
    assert proposals["proposals"][0]["release_gate"]["approved"] is False
    assert paths["ledger"].exists()
