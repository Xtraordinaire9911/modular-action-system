"""Runtime control tests for cross-source conflict detection."""

from src.runtime.cognitive_map import CognitiveMap
from src.verification.conflict_detector import ConflictDetector, ConflictRule


def test_conflict_detector_marks_disagreement_between_page_and_device():
    cognitive_map = CognitiveMap(task_id="task_1")
    cognitive_map.page_state = {"room_A": {"booked": True}}
    cognitive_map.device_states = {"room_A": {"occupied": False}}

    conflicts = ConflictDetector().detect(
        cognitive_map,
        [
            ConflictRule(
                conflict_type="room_readiness",
                left_source="page",
                left_path="room_A.booked",
                right_source="device",
                right_path="room_A.occupied",
            )
        ],
    )

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "room_readiness"
