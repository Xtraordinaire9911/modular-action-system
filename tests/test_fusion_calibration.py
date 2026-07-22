from evaluation.fusion_calibration import FusionTrial, calibrate_fusion_thresholds
from src.runtime.cognitive_map import CognitiveMap, StateAssertion
from src.verification.conflict_detector import EpistemicArbiter


def test_required_source_uncertainty_blocks_when_wot_evidence_is_missing():
    cognitive_map = CognitiveMap(task_id="missing-wot")
    cognitive_map.add_state_assertion(StateAssertion("thermostat", "target_temperature", 20, "dom", timestamp_ms=1000))
    arbiter = EpistemicArbiter(
        required_sources_by_attribute={"target_temperature": {"dom", "wot"}},
        missing_source_mass=1.2,
    )

    decision = arbiter.fuse(cognitive_map)

    assert not decision.allow_system1
    assert decision.active_perception_required
    assert decision.conflicts[0].conflict_type == "required_source_missing_or_stale"
    assert decision.conflicts[0].values["missing_sources"] == ["wot"]


def test_required_source_rule_accepts_complete_fresh_evidence():
    cognitive_map = CognitiveMap(task_id="complete")
    cognitive_map.add_state_assertion(StateAssertion("thermostat", "target_temperature", 20, "dom", timestamp_ms=1000))
    cognitive_map.add_state_assertion(StateAssertion("thermostat", "target_temperature", 20, "wot", timestamp_ms=1001))
    arbiter = EpistemicArbiter(
        required_sources_by_attribute={"target_temperature": {"dom", "wot"}},
    )

    assert arbiter.fuse(cognitive_map).allow_system1


def test_threshold_calibration_reports_false_halts_misses_and_recommendation():
    trials = [
        FusionTrial("clean", False, 0.0, 1.0, "DOM+WOT"),
        FusionTrial("layout", False, 0.1, 1.0, "DOM+WOT"),
        FusionTrial("stale", True, 1.6, 2.0, "DOM+WOT", "value_mismatch"),
        FusionTrial("offline", True, 1.0, 3.0, "DOM+WOT", "required_source_missing_or_stale"),
    ]

    report = calibrate_fusion_thresholds(trials, thresholds=[0.5, 1.0, 1.2])

    assert report["recommended_threshold"] in {0.5, 1.0}
    operating = report["recommended_operating_point"]
    assert operating["false_positive"] == 0
    assert operating["false_negative"] == 0
    assert report["source_confusion"]["DOM+WOT"]["missed"] == 0
