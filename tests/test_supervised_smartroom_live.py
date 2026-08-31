"""End-to-end proof for the shared supervised smart-room session.

This test deliberately uses the same ``run`` function as the weekly demo.  It
therefore checks the integration point the supervisor asked for, rather than a
second test-only orchestration path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.run_supervised_smartroom_demo import DEFAULT_UTTERANCE, run

pytestmark = pytest.mark.smartroom


@pytest.fixture()
def running_room() -> None:
    try:
        urllib.request.urlopen("http://127.0.0.1:3000", timeout=3).close()  # noqa: S310 - local fixture
        urllib.request.urlopen("http://127.0.0.1:8081/state", timeout=3).close()  # noqa: S310 - local fixture
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - environment guard
        pytest.skip(f"the smart-room services are not running: {exc}")


def test_shared_session_uses_dom_and_wot_then_restores_the_room(
    running_room: None,
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "episode.json"
    args = argparse.Namespace(
        utterance=DEFAULT_UTTERANCE,
        use_model=False,
        headed=False,
        auto_approve=True,
        step_delay=0.0,
        settle_delay=0.02,
        dashboard_url="http://127.0.0.1:3000",
        thing_directory_url="http://127.0.0.1:8082/things",
        wot_base_url="http://127.0.0.1:8080",
        control_url="http://127.0.0.1:8081",
        evidence=str(evidence),
    )

    assert asyncio.run(run(args)) == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))

    assert payload["result"]["state"] == "completed"
    assert payload["result"]["verified"] is True
    assert payload["selected_skill"] == "prepare_and_confirm_room"
    assert payload["surfaces_used"] == ["dom", "wot"]
    assert len(payload["result"]["primitive_plan"]) == 6
    assert payload["interventions"][0]["decision"] == "approve"
    assert payload["room_state_restored"] is True
    assert payload["os_input_isolation"] is False
