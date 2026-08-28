from __future__ import annotations

import json

import pytest

from evaluation.smart_room_recovery_campaign import run_smart_room_recovery_campaign


@pytest.mark.smartroom
def test_five_injected_scenes_recover_on_live_smart_room(tmp_path) -> None:
    paths = run_smart_room_recovery_campaign(tmp_path, headless=True)
    report = json.loads((tmp_path / "smart_room_recovery_report.json").read_text(encoding="utf-8"))

    assert paths["report"] == str(tmp_path / "smart_room_recovery_report.json")
    assert [scene["scene"] for scene in report["scenes"]] == [
        "Overlay obstruction",
        "Session expiry",
        "Optimistic rollback",
        "Dashboard/device disagreement",
        "Ineffective affordance",
    ]
    assert report["summary"] == {
        "scene_count": 5,
        "final_verified_count": 5,
        "all_final_oracles_verified": True,
        "fault_labels_hidden_from_planner": True,
    }
    assert all(scene["independent_oracle_verified"] for scene in report["scenes"])
